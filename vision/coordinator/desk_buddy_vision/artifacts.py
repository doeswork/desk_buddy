from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .storage import ArtifactRecord, VisionStorage, utc_now

SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_.-]+")
EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "application/x-npy": ".npy",
    "application/json": ".json",
    "application/octet-stream": ".bin",
}


def _safe_component(value: str) -> str:
    result = SAFE_COMPONENT.sub("_", str(value)).strip("._")
    if not result:
        raise ValueError("path component is empty after sanitization")
    return result


def _image_dimensions(payload: bytes, mime_type: str) -> tuple[int | None, int | None]:
    if mime_type == "image/png" and len(payload) >= 24 and payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return int.from_bytes(payload[16:20], "big"), int.from_bytes(payload[20:24], "big")
    if mime_type != "image/jpeg" or not payload.startswith(b"\xff\xd8"):
        return None, None
    index = 2
    while index + 9 < len(payload):
        if payload[index] != 0xFF:
            index += 1
            continue
        marker = payload[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(payload):
            break
        segment_length = int.from_bytes(payload[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(payload):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height = int.from_bytes(payload[index + 3 : index + 5], "big")
            width = int.from_bytes(payload[index + 5 : index + 7], "big")
            return width, height
        index += segment_length
    return None, None


class ArtifactStore:
    def __init__(self, root: str | Path, storage: VisionStorage) -> None:
        self.root = Path(root).expanduser().resolve()
        self.storage = storage
        self.tmp_root = self.root.parent / "tmp"
        self.root.mkdir(parents=True, exist_ok=True)
        self.tmp_root.mkdir(parents=True, exist_ok=True)

    def save_bytes(
        self,
        *,
        operation_id: str,
        robot_id: str,
        kind: str,
        payload: bytes,
        mime_type: str,
        artifact_id: str | None = None,
        width: int | None = None,
        height: int | None = None,
        dtype: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRecord:
        raw = bytes(payload)
        identifier = artifact_id or str(uuid.uuid4())
        extension = EXTENSIONS.get(mime_type)
        if extension is None:
            raise ValueError(f"unsupported artifact MIME type: {mime_type}")
        now = datetime.now(timezone.utc)
        directory = self.root / _safe_component(robot_id) / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}"
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"{_safe_component(identifier)}.{_safe_component(kind)}{extension}"
        target = (directory / filename).resolve()
        if self.root not in target.parents:
            raise ValueError("artifact target escaped storage root")
        if target.exists():
            raise FileExistsError(target)

        if (width is None or height is None) and mime_type.startswith("image/"):
            detected_width, detected_height = _image_dimensions(raw, mime_type)
            width = width if width is not None else detected_width
            height = height if height is not None else detected_height

        digest = hashlib.sha256(raw).hexdigest()
        temp_fd, temp_name = tempfile.mkstemp(prefix="artifact-", dir=self.tmp_root)
        try:
            with os.fdopen(temp_fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise

        record = ArtifactRecord(
            artifact_id=identifier,
            operation_id=operation_id,
            robot_id=robot_id,
            kind=kind,
            path=str(target),
            mime_type=mime_type,
            byte_size=len(raw),
            sha256=digest,
            width=width,
            height=height,
            dtype=dtype,
            model_id=model_id,
            model_version=model_version,
            metadata=dict(metadata or {}),
            created_at=utc_now(),
        )
        try:
            self.storage.register_artifact(record)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return record

    def read_bytes(self, artifact_id: str) -> tuple[ArtifactRecord, bytes]:
        record = self.storage.get_artifact(artifact_id)
        if record is None:
            raise KeyError(artifact_id)
        path = Path(record.path).resolve()
        if self.root not in path.parents:
            raise ValueError("artifact path is outside storage root")
        payload = path.read_bytes()
        if len(payload) != record.byte_size or hashlib.sha256(payload).hexdigest() != record.sha256:
            raise ValueError(f"artifact integrity check failed: {artifact_id}")
        return record, payload

    def cleanup_orphan_temporary_files(self) -> int:
        count = 0
        for path in self.tmp_root.iterdir():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
            count += 1
        return count


@dataclass
class UploadSession:
    transfer_id: str
    service_id: str
    job_id: str
    operation_id: str
    robot_id: str
    artifact_id: str
    kind: str
    mime_type: str
    byte_size: int
    sha256: str
    chunk_count: int
    chunk_size: int
    width: int | None
    height: int | None
    dtype: str | None
    model_id: str | None
    model_version: str | None
    metadata: dict[str, Any]
    directory: Path
    started_at: float = field(default_factory=time.monotonic)
    received: set[int] = field(default_factory=set)


class ArtifactUploadManager:
    """Validates worker uploads and commits only complete, hash-matched artifacts."""

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store
        self._sessions: dict[str, UploadSession] = {}
        self._lock = threading.RLock()

    def begin(self, metadata: Mapping[str, Any]) -> UploadSession:
        transfer_id = str(metadata.get("transfer_id") or "").strip()
        if not transfer_id:
            raise ValueError("transfer_id is required")
        with self._lock:
            if transfer_id in self._sessions:
                return self._sessions[transfer_id]
            chunk_count = int(metadata["chunk_count"])
            chunk_size = int(metadata.get("chunk_size", 4096))
            byte_size = int(metadata["byte_size"])
            if chunk_count < 1 or chunk_size < 256 or byte_size < 0:
                raise ValueError("invalid upload size metadata")
            directory = self.store.tmp_root / f"upload-{_safe_component(transfer_id)}"
            directory.mkdir(parents=True, exist_ok=False)
            session = UploadSession(
                transfer_id=transfer_id,
                service_id=str(metadata["service_id"]),
                job_id=str(metadata["job_id"]),
                operation_id=str(metadata["operation_id"]),
                robot_id=str(metadata["robot_id"]),
                artifact_id=str(metadata.get("artifact_id") or uuid.uuid4()),
                kind=str(metadata["kind"]),
                mime_type=str(metadata["mime_type"]),
                byte_size=byte_size,
                sha256=str(metadata["sha256"]).lower(),
                chunk_count=chunk_count,
                chunk_size=chunk_size,
                width=int(metadata["width"]) if metadata.get("width") is not None else None,
                height=int(metadata["height"]) if metadata.get("height") is not None else None,
                dtype=str(metadata["dtype"]) if metadata.get("dtype") is not None else None,
                model_id=str(metadata["model_id"]) if metadata.get("model_id") else None,
                model_version=str(metadata["model_version"]) if metadata.get("model_version") else None,
                metadata=dict(metadata.get("metadata") or {}),
                directory=directory,
            )
            self._sessions[transfer_id] = session
            return session

    def add_chunk(
        self,
        transfer_id: str,
        index: int,
        payload: bytes,
        *,
        service_id: str | None = None,
        job_id: str | None = None,
        operation_id: str | None = None,
        artifact_id: str | None = None,
    ) -> bool:
        with self._lock:
            session = self._sessions.get(transfer_id)
            if session is None:
                raise KeyError(transfer_id)
            expected = {
                "service_id": session.service_id,
                "job_id": session.job_id,
                "operation_id": session.operation_id,
                "artifact_id": session.artifact_id,
            }
            supplied = {
                "service_id": service_id,
                "job_id": job_id,
                "operation_id": operation_id,
                "artifact_id": artifact_id,
            }
            for field_name, value in supplied.items():
                if value is not None and str(value) != expected[field_name]:
                    raise PermissionError(f"upload chunk {field_name} does not match transfer")
            if index < 0 or index >= session.chunk_count:
                raise ValueError("chunk index is outside transfer bounds")
            target = session.directory / f"{index:08d}.part"
            raw = bytes(payload)
            if len(raw) > session.chunk_size:
                raise ValueError("upload chunk exceeds declared chunk_size")
            if target.exists():
                if target.read_bytes() != raw:
                    raise ValueError("duplicate chunk payload does not match")
                return False
            target.write_bytes(raw)
            session.received.add(index)
            return True

    def is_complete(self, transfer_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(transfer_id)
            if session is None:
                raise KeyError(transfer_id)
            return len(session.received) == session.chunk_count

    def finish(self, transfer_id: str) -> ArtifactRecord:
        with self._lock:
            session = self._sessions.get(transfer_id)
            if session is None:
                raise KeyError(transfer_id)
            if len(session.received) != session.chunk_count:
                raise ValueError("upload is incomplete")
            payload = b"".join((session.directory / f"{index:08d}.part").read_bytes() for index in range(session.chunk_count))
            if len(payload) != session.byte_size:
                raise ValueError("uploaded byte size does not match metadata")
            if hashlib.sha256(payload).hexdigest() != session.sha256:
                raise ValueError("uploaded SHA-256 does not match metadata")
            record = self.store.save_bytes(
                operation_id=session.operation_id,
                robot_id=session.robot_id,
                kind=session.kind,
                payload=payload,
                mime_type=session.mime_type,
                artifact_id=session.artifact_id,
                width=session.width,
                height=session.height,
                dtype=session.dtype,
                model_id=session.model_id,
                model_version=session.model_version,
                metadata=session.metadata,
            )
            shutil.rmtree(session.directory, ignore_errors=True)
            self._sessions.pop(transfer_id, None)
            return record

    def abort(self, transfer_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(transfer_id, None)
            if session is None:
                return False
            shutil.rmtree(session.directory, ignore_errors=True)
            return True

    def expire(self, *, max_age_seconds: float, now: float | None = None) -> list[str]:
        timestamp = time.monotonic() if now is None else float(now)
        with self._lock:
            expired = [
                transfer_id
                for transfer_id, session in self._sessions.items()
                if timestamp - session.started_at >= max_age_seconds
            ]
        for transfer_id in expired:
            self.abort(transfer_id)
        return expired
