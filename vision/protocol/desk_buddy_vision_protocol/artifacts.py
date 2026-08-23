from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from typing import Any, Mapping

MAGIC = b"DBV1"
MAX_HEADER_BYTES = 65_536


@dataclass(frozen=True)
class BinaryFrame:
    header: dict[str, Any]
    payload: bytes


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def encode_binary_frame(header: Mapping[str, Any], payload: bytes) -> bytes:
    header_bytes = json.dumps(dict(header), separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(header_bytes) > MAX_HEADER_BYTES:
        raise ValueError("binary frame header is too large")
    return MAGIC + struct.pack(">I", len(header_bytes)) + header_bytes + bytes(payload)


def decode_binary_frame(frame: bytes) -> BinaryFrame:
    raw = bytes(frame)
    if len(raw) < 8 or raw[:4] != MAGIC:
        raise ValueError("invalid binary frame magic")
    header_length = struct.unpack(">I", raw[4:8])[0]
    if header_length > MAX_HEADER_BYTES or len(raw) < 8 + header_length:
        raise ValueError("invalid binary frame header length")
    try:
        header = json.loads(raw[8 : 8 + header_length].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid binary frame JSON header") from exc
    if not isinstance(header, dict):
        raise ValueError("binary frame header must be an object")
    return BinaryFrame(header=header, payload=raw[8 + header_length :])


def iter_chunks(payload: bytes, *, chunk_size: int = 4096):
    if chunk_size < 256:
        raise ValueError("chunk_size must be at least 256 bytes")
    raw = bytes(payload)
    count = max(1, (len(raw) + chunk_size - 1) // chunk_size)
    for index in range(count):
        yield index, count, raw[index * chunk_size : (index + 1) * chunk_size]


class ChunkAssembler:
    def __init__(self, *, chunk_count: int, byte_size: int, sha256: str) -> None:
        if chunk_count < 1 or byte_size < 0:
            raise ValueError("invalid transfer metadata")
        self.chunk_count = chunk_count
        self.byte_size = byte_size
        self.sha256 = sha256.lower()
        self._chunks: dict[int, bytes] = {}

    def add(self, index: int, payload: bytes) -> bool:
        if index < 0 or index >= self.chunk_count:
            raise ValueError("chunk index is outside transfer bounds")
        duplicate = index in self._chunks
        if duplicate and self._chunks[index] != bytes(payload):
            raise ValueError("duplicate chunk payload does not match")
        self._chunks[index] = bytes(payload)
        return not duplicate

    @property
    def complete(self) -> bool:
        return len(self._chunks) == self.chunk_count

    def finish(self) -> bytes:
        if not self.complete:
            raise ValueError("transfer is incomplete")
        result = b"".join(self._chunks[index] for index in range(self.chunk_count))
        if len(result) != self.byte_size:
            raise ValueError("artifact byte size does not match metadata")
        if sha256_bytes(result) != self.sha256:
            raise ValueError("artifact SHA-256 does not match metadata")
        return result

