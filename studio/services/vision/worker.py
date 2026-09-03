"""Generic single-queue MQTT runtime for isolated vision model workers."""

from __future__ import annotations

import json
import hashlib
import logging
import queue
import socket
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .contracts import CONTRACT_SCHEMA, ContractError, JobEnvelope, JobEvent, WorkerStatus
from .frames import BinaryArtifact, DEFAULT_MAX_FRAME_BYTES, decode_artifact, encode_artifact
from .topics import VisionTopics

LOGGER = logging.getLogger(__name__)


class WorkerTransport(Protocol):
    def subscribe(self, topic: str, *, qos: int = 1) -> None: ...

    def publish(self, topic: str, payload: bytes | str | Mapping[str, Any], *, qos: int, retain: bool = False) -> bool: ...


@dataclass(frozen=True)
class GeneratedArtifact:
    role: str
    mime_type: str
    payload: bytes
    artifact_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkerOutput:
    payload: dict[str, Any]
    artifacts: tuple[GeneratedArtifact, ...] = ()


class JobHandler(Protocol):
    model_id: str
    model_version: str
    job_kinds: tuple[str, ...]
    input_schemas: tuple[str, ...]
    output_schemas: tuple[str, ...]

    def handle(self, job: JobEnvelope, artifacts: Mapping[str, BinaryArtifact]) -> WorkerOutput: ...


@dataclass
class _Pending:
    job: JobEnvelope
    handler: JobHandler
    received_at: float
    fingerprint: str
    artifacts: dict[str, BinaryArtifact] = field(default_factory=dict)
    queued: bool = False


@dataclass(frozen=True)
class _Terminal:
    event: dict[str, Any]
    artifacts: tuple[tuple[str, bytes], ...]
    fingerprint: str = ""
    pinned_until_ack: bool = False


class MQTTWorker:
    """Runs model handlers while MQTT remains the only application protocol."""

    def __init__(
        self,
        *,
        worker_id: str,
        worker_kind: str,
        transport: WorkerTransport,
        handlers: Mapping[str, JobHandler],
        topics: VisionTopics | None = None,
        device: str = "cpu",
        artifact_timeout_seconds: float = 120.0,
        max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
        terminal_cache_size: int = 128,
    ) -> None:
        self.worker_id = worker_id
        self.worker_kind = worker_kind
        self.transport = transport
        self.handlers = dict(handlers)
        self.topics = topics or VisionTopics()
        self.device = device
        self.artifact_timeout_seconds = max(1.0, float(artifact_timeout_seconds))
        self.max_frame_bytes = int(max_frame_bytes)
        self.terminal_cache_size = max(1, int(terminal_cache_size))
        self._pending: dict[str, _Pending] = {}
        self._early_artifacts: dict[str, dict[str, BinaryArtifact]] = {}
        self._terminal: OrderedDict[str, _Terminal] = OrderedDict()
        self._queue: queue.Queue[str | None] = queue.Queue(maxsize=32)
        self._active_job_id = ""
        self._stopping = threading.Event()
        self._lock = threading.RLock()
        self._thread = threading.Thread(target=self._run, name=f"{worker_id}-jobs", daemon=True)
        self._watchdog = threading.Thread(target=self._watch, name=f"{worker_id}-watchdog", daemon=True)

    def start(self) -> None:
        self.transport.subscribe(self.topics.request(self.worker_id), qos=1)
        self.transport.subscribe(self.topics.artifact_in(self.worker_id), qos=1)
        self.publish_status()
        self._thread.start()
        self._watchdog.start()

    def stop(self) -> None:
        self._stopping.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=5)
        self._watchdog.join(timeout=2)
        self.transport.publish(
            self.topics.status(self.worker_id),
            WorkerStatus.offline(self.worker_id, self.worker_kind).as_dict(),
            qos=1,
            retain=True,
        )

    def last_will(self) -> tuple[str, dict[str, Any]]:
        return self.topics.status(self.worker_id), WorkerStatus.offline(self.worker_id, self.worker_kind).as_dict()

    def register_handler(self, model_id: str, handler: JobHandler) -> None:
        with self._lock:
            self.handlers[model_id] = handler
        self.publish_status()

    def publish_status(self) -> None:
        with self._lock:
            unique = list({id(handler): handler for handler in self.handlers.values()}.values())
            models_list: list[dict[str, Any]] = []
            for handler in unique:
                advertised = getattr(handler, "advertised_models", None)
                if callable(advertised):
                    models_list.extend(dict(model) for model in advertised())
                elif handler.model_id != "*":
                    models_list.append({
                        "model_id": handler.model_id,
                        "model_version": handler.model_version,
                        "job_kinds": list(handler.job_kinds),
                    })
            models = tuple(models_list)
            status = WorkerStatus(
                worker_id=self.worker_id,
                worker_kind=self.worker_kind,
                host=socket.gethostname(),
                device=self.device,
                ready=True,
                busy=bool(self._active_job_id),
                job_kinds=tuple(sorted({kind for handler in unique for kind in handler.job_kinds})),
                models=models,
                input_schemas=tuple(sorted({schema for handler in unique for schema in handler.input_schemas})),
                output_schemas=tuple(sorted({schema for handler in unique for schema in handler.output_schemas})),
            )
        self.transport.publish(self.topics.status(self.worker_id), status.as_dict(), qos=1, retain=True)

    def handle_message(self, topic: str, payload: bytes) -> None:
        try:
            if topic == self.topics.request(self.worker_id):
                self._receive_job(payload)
            elif topic == self.topics.artifact_in(self.worker_id):
                self._receive_artifact(payload)
        except Exception:
            LOGGER.exception("worker %s rejected a message on %s", self.worker_id, topic)

    def _receive_job(self, payload: bytes) -> None:
        try:
            raw = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("invalid_json", "worker request must be a JSON object") from exc
        if not isinstance(raw, dict):
            raise ContractError("invalid_json", "worker request must be a JSON object")
        job = JobEnvelope.from_dict(raw)
        fingerprint = hashlib.sha256(
            json.dumps(job.as_dict(), separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        if job.worker_id != self.worker_id:
            raise ContractError("wrong_worker", "job was sent to a different worker")

        if job.kind == "job.status":
            self._status_request(job)
            return
        if job.kind == "artifact.resend":
            self._resend_request(job)
            return

        with self._lock:
            terminal = self._terminal.get(job.job_id)
            if terminal is not None:
                if terminal.fingerprint and terminal.fingerprint != fingerprint:
                    self._publish_duplicate_conflict(job, "completed job ID was reused with different content")
                    return
                self._republish_terminal(terminal)
                return
            pending = self._pending.get(job.job_id)
            if pending is not None:
                if pending.fingerprint != fingerprint:
                    self._publish_duplicate_conflict(job, "pending job ID was reused with different content")
                    return
                self._publish_event(job, pending.handler, "waiting_for_artifacts" if not pending.queued else "accepted")
                return
            handler = self.handlers.get(job.model_id) or self.handlers.get("*")
            if handler is None or job.kind not in handler.job_kinds:
                self._publish_failure(job, None, "unsupported_model", f"{job.model_id} does not support {job.kind}")
                return
            early = self._early_artifacts.pop(job.job_id, {})
            pending = _Pending(job=job, handler=handler, received_at=time.monotonic(), fingerprint=fingerprint, artifacts=early)
            self._pending[job.job_id] = pending
            self._publish_event(job, handler, "accepted")
            self._queue_if_ready(pending)

    def _receive_artifact(self, payload: bytes) -> None:
        artifact = decode_artifact(payload, max_frame_bytes=self.max_frame_bytes)
        if not artifact.job_id or not artifact.role:
            raise ContractError("invalid_frame", "artifact requires job_id and role")
        with self._lock:
            pending = self._pending.get(artifact.job_id)
            target = pending.artifacts if pending is not None else self._early_artifacts.setdefault(artifact.job_id, {})
            existing = target.get(artifact.role)
            if existing is not None:
                if existing.sha256 != artifact.sha256:
                    if pending is not None:
                        self._finish_failure(pending, "artifact_conflict", f"conflicting artifact role {artifact.role}")
                    raise ContractError("artifact_conflict", f"conflicting artifact role {artifact.role}")
                return
            target[artifact.role] = artifact
            if pending is not None:
                self._queue_if_ready(pending)

    def _queue_if_ready(self, pending: _Pending) -> None:
        if pending.queued:
            return
        missing = [role for role in pending.job.input_artifact_roles if role not in pending.artifacts]
        if missing:
            self._publish_event(pending.job, pending.handler, "waiting_for_artifacts", {"missing_roles": missing})
            return
        try:
            self._queue.put_nowait(pending.job.job_id)
            pending.queued = True
        except queue.Full:
            self._finish_failure(pending, "worker_busy", "worker queue is full")

    def _run(self) -> None:
        while not self._stopping.is_set():
            job_id = self._queue.get()
            if job_id is None:
                return
            with self._lock:
                pending = self._pending.get(job_id)
                if pending is None:
                    continue
                self._active_job_id = job_id
            self.publish_status()
            self._publish_event(pending.job, pending.handler, "processing")
            started_at = time.monotonic()
            try:
                output = pending.handler.handle(pending.job, dict(pending.artifacts))
                frames: list[tuple[str, bytes]] = []
                artifact_ids: dict[str, str] = {}
                for generated in output.artifacts:
                    artifact_id = generated.artifact_id or f"{pending.job.job_id}-{generated.role}-{uuid.uuid4().hex[:8]}"
                    frame = encode_artifact(
                        job_id=pending.job.job_id,
                        artifact_id=artifact_id,
                        role=generated.role,
                        mime_type=generated.mime_type,
                        payload=generated.payload,
                        metadata=generated.metadata,
                        max_frame_bytes=self.max_frame_bytes,
                    )
                    frames.append((artifact_id, frame))
                    artifact_ids[generated.role] = artifact_id
                    self.transport.publish(self.topics.artifact_out(self.worker_id), frame, qos=1)
                result = dict(output.payload)
                result["timing_ms"] = round((time.monotonic() - started_at) * 1000.0, 3)
                if artifact_ids:
                    result["artifacts"] = artifact_ids
                event = self._event(pending.job, pending.handler, "completed", result).as_dict()
                terminal = _Terminal(
                    event=event,
                    artifacts=tuple(frames),
                    fingerprint=pending.fingerprint,
                    pinned_until_ack=pending.job.kind == "ik_model.train" and bool(frames),
                )
                with self._lock:
                    self._remember_terminal(pending.job.job_id, terminal)
                    self._pending.pop(pending.job.job_id, None)
            except Exception as exc:
                LOGGER.exception("vision worker job %s failed", pending.job.job_id)
                self._finish_failure(pending, "job_failed", str(exc))
                terminal = None
            finally:
                with self._lock:
                    self._active_job_id = ""
                self.publish_status()
            if terminal is not None:
                self.transport.publish(self.topics.event(self.worker_id), terminal.event, qos=1)

    def _watch(self) -> None:
        while not self._stopping.wait(0.5):
            now = time.monotonic()
            with self._lock:
                expired = [
                    pending
                    for pending in self._pending.values()
                    if not pending.queued and now - pending.received_at > self.artifact_timeout_seconds
                ]
            for pending in expired:
                self._finish_failure(pending, "artifact_timeout", "input artifacts did not arrive before timeout")

    def _status_request(self, job: JobEnvelope) -> None:
        if bool(job.payload.get("probe")):
            self.publish_status()
            event = self._event(job, None, "completed", {
                "healthy": True,
                "worker_id": self.worker_id,
                "worker_kind": self.worker_kind,
                "device": self.device,
            })
            self.transport.publish(self.topics.event(self.worker_id), event.as_dict(), qos=1)
            return
        target = str(job.payload.get("target_job_id") or job.job_id)
        with self._lock:
            terminal = self._terminal.get(target)
            pending = self._pending.get(target)
            if terminal is not None and bool(job.payload.get("acknowledge_artifacts")):
                self._terminal.pop(target, None)
        if terminal is not None and bool(job.payload.get("acknowledge_artifacts")):
            event = self._event(job, None, "completed", {"acknowledged_job_id": target})
            self.transport.publish(self.topics.event(self.worker_id), event.as_dict(), qos=1)
            return
        if terminal is not None:
            self._republish_terminal(terminal)
        elif pending is not None:
            self._publish_event(job, pending.handler, "processing" if target == self._active_job_id else "accepted")
        else:
            self._publish_failure(job, None, "job_not_found", f"job {target} is not available")

    def _resend_request(self, job: JobEnvelope) -> None:
        target_job = str(job.payload.get("target_job_id") or "")
        artifact_id = str(job.payload.get("artifact_id") or "")
        with self._lock:
            terminal = self._terminal.get(target_job)
        if terminal is None:
            self._publish_failure(job, None, "job_not_found", f"job {target_job} is not available")
            return
        matches = [frame for candidate, frame in terminal.artifacts if not artifact_id or candidate == artifact_id]
        if not matches:
            self._publish_failure(job, None, "artifact_not_found", f"artifact {artifact_id} is not available")
            return
        for frame in matches:
            self.transport.publish(self.topics.artifact_out(self.worker_id), frame, qos=1)
        event = self._event(job, None, "completed", {"resent": len(matches), "target_job_id": target_job})
        self.transport.publish(self.topics.event(self.worker_id), event.as_dict(), qos=1)

    def _republish_terminal(self, terminal: _Terminal) -> None:
        for _, frame in terminal.artifacts:
            self.transport.publish(self.topics.artifact_out(self.worker_id), frame, qos=1)
        self.transport.publish(self.topics.event(self.worker_id), terminal.event, qos=1)

    def _finish_failure(self, pending: _Pending, code: str, message: str) -> None:
        event = self._event(pending.job, pending.handler, "failed", error={"code": code, "message": message}).as_dict()
        with self._lock:
            self._pending.pop(pending.job.job_id, None)
            self._remember_terminal(pending.job.job_id, _Terminal(event, (), pending.fingerprint))
        self.transport.publish(self.topics.event(self.worker_id), event, qos=1)

    def _publish_failure(self, job: JobEnvelope, handler: JobHandler | None, code: str, message: str) -> None:
        event = self._event(job, handler, "failed", error={"code": code, "message": message}).as_dict()
        with self._lock:
            self._remember_terminal(job.job_id, _Terminal(event, ()))
        self.transport.publish(self.topics.event(self.worker_id), event, qos=1)

    def _publish_duplicate_conflict(self, job: JobEnvelope, message: str) -> None:
        event = self._event(job, None, "failed", error={"code": "duplicate_job_conflict", "message": message})
        self.transport.publish(self.topics.event(self.worker_id), event.as_dict(), qos=1)

    def _publish_event(
        self,
        job: JobEnvelope,
        handler: JobHandler | None,
        status: str,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        self.transport.publish(
            self.topics.event(self.worker_id),
            self._event(job, handler, status, payload).as_dict(),
            qos=1,
        )

    def _event(
        self,
        job: JobEnvelope,
        handler: JobHandler | None,
        status: str,
        payload: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> JobEvent:
        version = str(getattr(handler, "model_version", ""))
        version_for = getattr(handler, "version_for", None)
        if callable(version_for):
            version = str(version_for(job.model_id))
        return JobEvent(
            job_id=job.job_id,
            request_id=job.request_id,
            capture_id=job.capture_id,
            robot_id=job.robot_id,
            worker_id=self.worker_id,
            kind=job.kind,
            model_id=job.model_id,
            model_version=version,
            status=status,
            payload=dict(payload or {}),
            error=dict(error) if error else None,
        )

    def _remember_terminal(self, job_id: str, terminal: _Terminal) -> None:
        self._terminal[job_id] = terminal
        self._terminal.move_to_end(job_id)
        while len(self._terminal) > self.terminal_cache_size:
            removable = next((key for key, value in self._terminal.items() if not value.pinned_until_ack), None)
            if removable is None:
                break
            self._terminal.pop(removable, None)
