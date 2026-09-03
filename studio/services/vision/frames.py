"""Single-packet DBV1 binary artifacts used across MQTT worker boundaries."""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import CONTRACT_SCHEMA, ContractError

MAGIC = b"DBV1"
MAX_HEADER_BYTES = 65_536
DEFAULT_MAX_FRAME_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class BinaryArtifact:
    job_id: str
    artifact_id: str
    role: str
    mime_type: str
    payload: bytes
    metadata: dict[str, Any]
    sha256: str


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def encode_artifact(
    *,
    job_id: str,
    artifact_id: str,
    role: str,
    mime_type: str,
    payload: bytes,
    metadata: Mapping[str, Any] | None = None,
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
) -> bytes:
    body = bytes(payload)
    header = {
        "schema": CONTRACT_SCHEMA,
        "job_id": str(job_id),
        "artifact_id": str(artifact_id),
        "role": str(role),
        "mime_type": str(mime_type),
        "byte_size": len(body),
        "sha256": digest(body),
        "metadata": dict(metadata or {}),
    }
    encoded = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_HEADER_BYTES:
        raise ContractError("header_too_large", "binary artifact header exceeds 65536 bytes")
    frame = MAGIC + struct.pack(">I", len(encoded)) + encoded + body
    if len(frame) > max_frame_bytes:
        raise ContractError("frame_too_large", f"binary artifact exceeds {max_frame_bytes} bytes")
    return frame


def decode_artifact(frame: bytes, *, max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES) -> BinaryArtifact:
    raw = bytes(frame)
    if len(raw) > max_frame_bytes:
        raise ContractError("frame_too_large", f"binary artifact exceeds {max_frame_bytes} bytes")
    if len(raw) < 8 or raw[:4] != MAGIC:
        raise ContractError("invalid_frame", "invalid DBV1 magic")
    header_length = struct.unpack(">I", raw[4:8])[0]
    if header_length > MAX_HEADER_BYTES or len(raw) < 8 + header_length:
        raise ContractError("invalid_frame", "invalid DBV1 header length")
    try:
        header = json.loads(raw[8 : 8 + header_length].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("invalid_frame", "DBV1 header is not valid JSON") from exc
    if not isinstance(header, dict) or header.get("schema") != CONTRACT_SCHEMA:
        raise ContractError("unsupported_schema", f"artifact schema must be {CONTRACT_SCHEMA}")
    payload = raw[8 + header_length :]
    if int(header.get("byte_size", -1)) != len(payload):
        raise ContractError("size_mismatch", "artifact byte size does not match its header")
    expected = str(header.get("sha256") or "").lower()
    actual = digest(payload)
    if expected != actual:
        raise ContractError("hash_mismatch", "artifact SHA-256 does not match its header")
    metadata = header.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ContractError("invalid_frame", "artifact metadata must be an object")
    return BinaryArtifact(
        job_id=str(header.get("job_id") or ""),
        artifact_id=str(header.get("artifact_id") or ""),
        role=str(header.get("role") or ""),
        mime_type=str(header.get("mime_type") or "application/octet-stream"),
        payload=payload,
        metadata=dict(metadata),
        sha256=actual,
    )

