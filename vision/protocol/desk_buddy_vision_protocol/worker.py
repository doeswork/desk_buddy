from __future__ import annotations

import json
import logging
import queue
import socket
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from .artifacts import ChunkAssembler, decode_binary_frame, encode_binary_frame, iter_chunks, sha256_bytes
from .envelopes import JOB_SCHEMA, ContractError, JobEnvelope, JobResult, decode_json_object, utc_now
from .jobs import ServiceCapability, TopicLayout

LOGGER = logging.getLogger(__name__)


class WorkerTransport(Protocol):
    def subscribe(self, topic: str, *, qos: int = 1) -> None: ...

    def publish(self, topic: str, payload: bytes | str | dict[str, Any], *, qos: int, retain: bool = False) -> bool: ...


@dataclass(frozen=True)
class ArtifactInput:
    artifact_id: str
    payload: bytes
    metadata: dict[str, Any]


@dataclass(frozen=True)
class GeneratedArtifact:
    role: str
    kind: str
    mime_type: str
    payload: bytes
    width: int | None = None
    height: int | None = None
    dtype: str | None = None
    source_model_id: str | None = None
    source_model_version: str | None = None
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

    def handle(self, job: JobEnvelope, artifacts: Mapping[str, ArtifactInput]) -> WorkerOutput: ...


@dataclass
class _Download:
    artifact_id: str
    transfer_id: str
    event: threading.Event = field(default_factory=threading.Event)
    metadata: dict[str, Any] | None = None
    assembler: ChunkAssembler | None = None
    result: ArtifactInput | None = None
    error: Exception | None = None


@dataclass
class _Upload:
    artifact_id: str
    transfer_id: str
    accepted: threading.Event = field(default_factory=threading.Event)
    completed: threading.Event = field(default_factory=threading.Event)
    error: Exception | None = None


class MQTTWorker:
    """Single-queue worker with MQTT artifact transfer and job idempotency."""

    def __init__(
        self,
        *,
        service_id: str,
        service_kind: str,
        transport: WorkerTransport,
        handlers: Mapping[str, JobHandler],
        topics: TopicLayout | None = None,
        compute_device: str = "cpu",
        artifact_timeout_seconds: float = 120.0,
        chunk_size: int = 4096,
        terminal_cache_size: int = 256,
    ) -> None:
        self.service_id = service_id
        self.service_kind = service_kind
        self.transport = transport
        self.handlers = dict(handlers)
        self.topics = topics or TopicLayout()
        self.compute_device = compute_device
        self.artifact_timeout_seconds = artifact_timeout_seconds
        self.chunk_size = chunk_size
        self.terminal_cache_size = terminal_cache_size
        self._jobs: queue.Queue[JobEnvelope | None] = queue.Queue(maxsize=32)
        self._thread = threading.Thread(target=self._run, name=f"{service_id}-jobs", daemon=True)
        self._downloads: dict[str, _Download] = {}
        self._uploads: dict[str, _Upload] = {}
        self._terminal: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._known_job_ids: set[str] = set()
        self._active_job_id: str | None = None
        self._lock = threading.RLock()
        self._stopping = threading.Event()

    def start(self) -> None:
        self.transport.subscribe(self.topics.service_request(self.service_id), qos=1)
        self.transport.subscribe(self.topics.artifact_metadata(self.service_id), qos=1)
        self.transport.subscribe(self.topics.artifact_download(self.service_id), qos=1)
        self.publish_status()
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._jobs.put(None)
        self._thread.join(timeout=10)
        self.transport.publish(
            self.topics.service_status(self.service_id),
            ServiceCapability.offline(self.service_id, self.service_kind).as_dict(),
            qos=1,
            retain=True,
        )

    def last_will(self) -> tuple[str, dict[str, Any]]:
        capability = ServiceCapability.offline(self.service_id, self.service_kind)
        return self.topics.service_status(self.service_id), capability.as_dict()

    def publish_status(self) -> None:
        with self._lock:
            models = tuple(
                {
                    "model_id": model_id,
                    "model_version": handler.model_version,
                    "job_kinds": list(handler.job_kinds),
                }
                for model_id, handler in sorted(self.handlers.items())
            )
            input_schemas = tuple(sorted({schema for handler in self.handlers.values() for schema in handler.input_schemas}))
            output_schemas = tuple(sorted({schema for handler in self.handlers.values() for schema in handler.output_schemas}))
            capability = ServiceCapability(
                service_id=self.service_id,
                service_kind=self.service_kind,
                state="online",
                ready=True,
                busy=self._active_job_id is not None,
                models=models,
                input_schemas=input_schemas,
                output_schemas=output_schemas,
                hostname=socket.gethostname(),
                compute_device=self.compute_device,
                updated_at=utc_now(),
            )
        self.transport.publish(self.topics.service_status(self.service_id), capability.as_dict(), qos=1, retain=True)

    def handle_message(self, topic: str, payload: bytes) -> None:
        try:
            if topic == self.topics.service_request(self.service_id):
                self._handle_job(payload)
            elif topic == self.topics.artifact_metadata(self.service_id):
                self._handle_artifact_control(payload)
            elif topic == self.topics.artifact_download(self.service_id):
                self._handle_download_chunk(payload)
        except Exception:
            LOGGER.exception("worker %s rejected message on %s", self.service_id, topic)

    def _handle_job(self, payload: bytes) -> None:
        job = JobEnvelope.from_json(payload)
        if job.service_id != self.service_id:
            raise ContractError("wrong_service", "job was delivered to a different service")
        with self._lock:
            terminal = self._terminal.get(job.job_id)
            if terminal is not None:
                self.transport.publish(self.topics.service_event(self.service_id), terminal, qos=1)
                return
            if job.job_id in self._known_job_ids:
                return
        handler = self.handlers.get(job.model_id)
        if handler is None or job.kind not in handler.job_kinds:
            self._publish_failure(job, "unsupported_model", f"{job.model_id} does not support {job.kind}")
            return
        try:
            with self._lock:
                self._jobs.put_nowait(job)
                self._known_job_ids.add(job.job_id)
        except queue.Full:
            self._publish_failure(job, "service_busy", "worker queue is full")
            return
        accepted = self._result(job, handler, status="accepted", payload={"queued": True})
        self.transport.publish(self.topics.service_event(self.service_id), accepted.as_dict(), qos=1)

    def _run(self) -> None:
        while not self._stopping.is_set():
            job = self._jobs.get()
            if job is None:
                return
            handler = self.handlers[job.model_id]
            with self._lock:
                self._active_job_id = job.job_id
            self.publish_status()
            self.transport.publish(
                self.topics.service_event(self.service_id),
                self._result(job, handler, status="processing", payload={}).as_dict(),
                qos=1,
            )
            try:
                inputs = {artifact_id: self._download(job, artifact_id) for artifact_id in job.input_artifact_ids}
                output = handler.handle(job, inputs)
                uploaded: dict[str, str] = {}
                for artifact in output.artifacts:
                    uploaded[artifact.role] = self._upload(job, handler, artifact)
                result_payload = dict(output.payload)
                if uploaded:
                    result_payload["artifacts"] = uploaded
                terminal = self._result(job, handler, status="completed", payload=result_payload).as_dict()
            except Exception as exc:
                LOGGER.exception("worker job %s failed", job.job_id)
                terminal = self._result(
                    job,
                    handler,
                    status="failed",
                    payload={},
                    error={"code": "job_failed", "message": str(exc)},
                ).as_dict()
            with self._lock:
                self._remember_terminal(job.job_id, terminal)
                self._active_job_id = None
            self.transport.publish(self.topics.service_event(self.service_id), terminal, qos=1)
            self.publish_status()

    def _download(self, job: JobEnvelope, artifact_id: str) -> ArtifactInput:
        transfer = _Download(artifact_id=artifact_id, transfer_id=str(uuid.uuid4()))
        with self._lock:
            self._downloads[transfer.transfer_id] = transfer
        request = {
            "schema": JOB_SCHEMA,
            "kind": "artifact.download.request",
            "service_id": self.service_id,
            "request_id": job.request_id,
            "operation_id": job.operation_id,
            "job_id": job.job_id,
            "transfer_id": transfer.transfer_id,
            "artifact_id": artifact_id,
        }
        self.transport.publish(self.topics.artifact_request(), request, qos=1)
        if not transfer.event.wait(self.artifact_timeout_seconds):
            with self._lock:
                self._downloads.pop(transfer.transfer_id, None)
            raise TimeoutError(f"artifact download timed out: {artifact_id}")
        if transfer.error:
            raise transfer.error
        if transfer.result is None:
            raise RuntimeError("artifact download completed without data")
        return transfer.result

    def _upload(self, job: JobEnvelope, handler: JobHandler, artifact: GeneratedArtifact) -> str:
        raw = bytes(artifact.payload)
        artifact_id = str(uuid.uuid4())
        transfer = _Upload(artifact_id=artifact_id, transfer_id=str(uuid.uuid4()))
        chunks = list(iter_chunks(raw, chunk_size=self.chunk_size))
        with self._lock:
            self._uploads[transfer.transfer_id] = transfer
        begin = {
            "schema": JOB_SCHEMA,
            "kind": "artifact.upload.begin",
            "service_id": self.service_id,
            "request_id": job.request_id,
            "operation_id": job.operation_id,
            "job_id": job.job_id,
            "transfer_id": transfer.transfer_id,
            "artifact_id": artifact_id,
            "artifact_kind": artifact.kind,
            "mime_type": artifact.mime_type,
            "byte_size": len(raw),
            "sha256": sha256_bytes(raw),
            "chunk_size": self.chunk_size,
            "chunk_count": len(chunks),
            "width": artifact.width,
            "height": artifact.height,
            "dtype": artifact.dtype,
            "model_id": artifact.source_model_id or handler.model_id,
            "model_version": artifact.source_model_version or handler.model_version,
            "metadata": dict(artifact.metadata),
        }
        self.transport.publish(self.topics.artifact_request(), begin, qos=1)
        if not transfer.accepted.wait(self.artifact_timeout_seconds):
            raise TimeoutError(f"artifact upload was not accepted: {artifact.role}")
        if transfer.error:
            raise transfer.error
        for index, count, chunk in chunks:
            header = {
                "schema": JOB_SCHEMA,
                "kind": "artifact.upload.chunk",
                "service_id": self.service_id,
                "operation_id": job.operation_id,
                "job_id": job.job_id,
                "transfer_id": transfer.transfer_id,
                "artifact_id": artifact_id,
                "chunk_index": index,
                "chunk_count": count,
            }
            self.transport.publish(
                self.topics.artifact_upload(self.service_id), encode_binary_frame(header, chunk), qos=1
            )
        if not transfer.completed.wait(self.artifact_timeout_seconds):
            raise TimeoutError(f"artifact upload did not complete: {artifact.role}")
        if transfer.error:
            raise transfer.error
        with self._lock:
            self._uploads.pop(transfer.transfer_id, None)
        return artifact_id

    def _handle_artifact_control(self, payload: bytes) -> None:
        body = decode_json_object(payload)
        transfer_id = str(body.get("transfer_id") or "")
        kind = str(body.get("kind") or "")
        with self._lock:
            download = self._downloads.get(transfer_id)
            upload = self._uploads.get(transfer_id)
        if kind == "artifact.download.metadata" and download:
            download.metadata = body
            download.assembler = ChunkAssembler(
                chunk_count=int(body["chunk_count"]),
                byte_size=int(body["byte_size"]),
                sha256=str(body["sha256"]),
            )
        elif kind == "artifact.upload.accepted" and upload:
            upload.accepted.set()
        elif kind == "artifact.upload.completed" and upload:
            upload.completed.set()
        elif kind.endswith(".rejected"):
            error = RuntimeError(str((body.get("error") or {}).get("message") or "artifact transfer rejected"))
            if download:
                download.error = error
                download.event.set()
            if upload:
                upload.error = error
                upload.accepted.set()
                upload.completed.set()

    def _handle_download_chunk(self, payload: bytes) -> None:
        frame = decode_binary_frame(payload)
        transfer_id = str(frame.header.get("transfer_id") or "")
        with self._lock:
            transfer = self._downloads.get(transfer_id)
        if transfer is None or transfer.assembler is None or transfer.metadata is None:
            return
        transfer.assembler.add(int(frame.header["chunk_index"]), frame.payload)
        if transfer.assembler.complete:
            try:
                data = transfer.assembler.finish()
                transfer.result = ArtifactInput(
                    artifact_id=transfer.artifact_id,
                    payload=data,
                    metadata=dict(transfer.metadata),
                )
            except Exception as exc:
                transfer.error = exc
            finally:
                with self._lock:
                    self._downloads.pop(transfer_id, None)
                transfer.event.set()

    def _publish_failure(self, job: JobEnvelope, code: str, message: str) -> None:
        fallback = next(iter(self.handlers.values()), None)
        result = JobResult(
            request_id=job.request_id,
            operation_id=job.operation_id,
            job_id=job.job_id,
            service_id=self.service_id,
            kind=job.kind,
            model_id=job.model_id,
            model_version=fallback.model_version if fallback else "unknown",
            status="failed",
            error={"code": code, "message": message},
        ).as_dict()
        with self._lock:
            self._remember_terminal(job.job_id, result)
        self.transport.publish(self.topics.service_event(self.service_id), result, qos=1)

    def _result(
        self,
        job: JobEnvelope,
        handler: JobHandler,
        *,
        status: str,
        payload: dict[str, Any],
        error: dict[str, Any] | None = None,
    ) -> JobResult:
        return JobResult(
            request_id=job.request_id,
            operation_id=job.operation_id,
            job_id=job.job_id,
            service_id=self.service_id,
            kind=job.kind,
            model_id=job.model_id,
            model_version=handler.model_version,
            status=status,
            payload=payload,
            error=error,
        )

    def _remember_terminal(self, job_id: str, result: dict[str, Any]) -> None:
        self._known_job_ids.add(job_id)
        self._terminal[job_id] = result
        self._terminal.move_to_end(job_id)
        while len(self._terminal) > self.terminal_cache_size:
            expired_job_id, _ = self._terminal.popitem(last=False)
            if expired_job_id != self._active_job_id:
                self._known_job_ids.discard(expired_job_id)
