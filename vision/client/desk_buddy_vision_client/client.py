from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from desk_buddy_vision_protocol import TopicLayout, VisionRequest, decode_binary_frame
from desk_buddy_vision_protocol.artifacts import ChunkAssembler
from desk_buddy_vision_protocol.envelopes import decode_json_object


@dataclass(frozen=True)
class OperationResult:
    request_id: str
    operation_id: str
    status: str
    stage: str
    payload: dict[str, Any]
    error: dict[str, Any] | None


@dataclass(frozen=True)
class ArtifactResult:
    request_id: str
    artifact_id: str
    mime_type: str
    payload: bytes
    sha256: str


@dataclass
class _PendingOperation:
    event: threading.Event = field(default_factory=threading.Event)
    result: OperationResult | None = None


@dataclass
class _PendingArtifact:
    artifact_id: str = ""
    mime_type: str = ""
    sha256: str = ""
    assembler: ChunkAssembler | None = None
    event: threading.Event = field(default_factory=threading.Event)
    result: ArtifactResult | None = None
    error: Exception | None = None


class VisionMQTTClient:
    """GUI-facing client that never subscribes to firmware or internal worker topics."""

    def __init__(
        self,
        *,
        robot_id: str,
        sender: str,
        transport: Any,
        topics: TopicLayout | None = None,
    ) -> None:
        self.robot_id = robot_id
        self.sender = sender
        self.transport = transport
        self.topics = topics or TopicLayout()
        self._operations: dict[str, _PendingOperation] = {}
        self._artifacts: dict[str, _PendingArtifact] = {}
        self._callbacks: list[Callable[[OperationResult], None]] = []
        self._status_callbacks: list[Callable[[dict[str, Any]], None]] = []
        self._status: dict[str, Any] | None = None
        self._robot_status: dict[str, Any] | None = None
        self._lock = threading.RLock()

    def start(self) -> None:
        self.transport.set_message_handler(self.handle_message)
        self.transport.subscribe(self.topics.vision_event(self.robot_id), qos=1)
        self.transport.subscribe(self.topics.vision_artifact_chunk(self.robot_id), qos=1)
        self.transport.subscribe(self.topics.vision_status(self.robot_id), qos=1)
        self.transport.subscribe(self.topics.coordinator_status(), qos=1)

    def add_event_callback(self, callback: Callable[[OperationResult], None]) -> None:
        self._callbacks.append(callback)

    def add_status_callback(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._status_callbacks.append(callback)

    @property
    def coordinator_status(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._status) if self._status is not None else None

    def request(self, kind: str, payload: Mapping[str, Any] | None = None, *, request_id: str | None = None) -> str:
        identifier = request_id or str(uuid.uuid4())
        request = VisionRequest(
            request_id=identifier,
            robot_id=self.robot_id,
            sender=self.sender,
            kind=kind,
            payload=dict(payload or {}),
        )
        with self._lock:
            if identifier in self._operations:
                raise ValueError(f"request_id is already pending: {identifier}")
            self._operations[identifier] = _PendingOperation()
        if not self.transport.publish(self.topics.vision_request(self.robot_id), request.as_dict(), qos=1):
            with self._lock:
                self._operations.pop(identifier, None)
            raise RuntimeError("vision request publish failed")
        return identifier

    def wait(self, request_id: str, *, timeout: float = 120.0) -> OperationResult:
        with self._lock:
            pending = self._operations.get(request_id)
        if pending is None:
            raise KeyError(request_id)
        if not pending.event.wait(timeout):
            raise TimeoutError(
                f"vision request timed out: {request_id}; physical operations must not be automatically resent"
            )
        assert pending.result is not None
        with self._lock:
            self._operations.pop(request_id, None)
        return pending.result

    def request_artifact(self, artifact_id: str, *, request_id: str | None = None) -> str:
        identifier = request_id or str(uuid.uuid4())
        with self._lock:
            self._artifacts[identifier] = _PendingArtifact(artifact_id=artifact_id)
        try:
            self.request("artifact_get", {"artifact_id": artifact_id}, request_id=identifier)
        except Exception:
            with self._lock:
                self._artifacts.pop(identifier, None)
            raise
        return identifier

    def wait_artifact(self, request_id: str, *, timeout: float = 120.0) -> ArtifactResult:
        with self._lock:
            pending = self._artifacts.get(request_id)
        if pending is None:
            raise KeyError(request_id)
        if not pending.event.wait(timeout):
            raise TimeoutError(f"artifact request timed out: {request_id}")
        if pending.error:
            raise pending.error
        assert pending.result is not None
        with self._lock:
            self._artifacts.pop(request_id, None)
            self._operations.pop(request_id, None)
        return pending.result

    def handle_message(self, topic: str, payload: bytes) -> None:
        if topic == self.topics.vision_event(self.robot_id):
            self._handle_event(payload)
        elif topic == self.topics.vision_artifact_chunk(self.robot_id):
            self._handle_artifact_chunk(payload)
        elif topic in {self.topics.vision_status(self.robot_id), self.topics.coordinator_status()}:
            status = decode_json_object(payload)
            with self._lock:
                if topic == self.topics.coordinator_status():
                    self._status = dict(status)
                else:
                    self._robot_status = dict(status)
                    if self._status is None:
                        self._status = dict(status)
            for callback in self._status_callbacks:
                callback(dict(status))

    def _handle_event(self, payload: bytes) -> None:
        body = decode_json_object(payload)
        request_id = str(body.get("request_id") or "")
        result = OperationResult(
            request_id=request_id,
            operation_id=str(body.get("operation_id") or ""),
            status=str(body.get("status") or ""),
            stage=str(body.get("stage") or ""),
            payload=dict(body.get("payload") or {}),
            error=dict(body["error"]) if isinstance(body.get("error"), dict) else None,
        )
        with self._lock:
            pending = self._operations.get(request_id)
            artifact = self._artifacts.get(request_id)
        if artifact and result.payload.get("kind") == "artifact.metadata":
            artifact.artifact_id = str(result.payload["artifact_id"])
            artifact.mime_type = str(result.payload["mime_type"])
            artifact.sha256 = str(result.payload["sha256"])
            artifact.assembler = ChunkAssembler(
                chunk_count=int(result.payload["chunk_count"]),
                byte_size=int(result.payload["byte_size"]),
                sha256=artifact.sha256,
            )
        if pending and result.status in {"completed", "failed", "cancelled"}:
            pending.result = result
            pending.event.set()
            if artifact and result.status != "completed" and artifact.result is None:
                artifact.error = RuntimeError(str((result.error or {}).get("message") or "artifact request failed"))
                artifact.event.set()
        for callback in self._callbacks:
            callback(result)

    def _handle_artifact_chunk(self, payload: bytes) -> None:
        frame = decode_binary_frame(payload)
        request_id = str(frame.header.get("request_id") or "")
        with self._lock:
            pending = self._artifacts.get(request_id)
        if pending is None or pending.assembler is None:
            return
        try:
            pending.assembler.add(int(frame.header["chunk_index"]), frame.payload)
            if pending.assembler.complete:
                data = pending.assembler.finish()
                pending.result = ArtifactResult(
                    request_id=request_id,
                    artifact_id=pending.artifact_id,
                    mime_type=pending.mime_type,
                    payload=data,
                    sha256=pending.sha256,
                )
                pending.event.set()
        except Exception as exc:
            pending.error = exc
            pending.event.set()
