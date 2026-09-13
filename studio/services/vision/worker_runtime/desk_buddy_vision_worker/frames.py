"""Binary JPEG envelope shared with the ESP32 photo protocol."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

PAYLOAD_MARKER = b',"payload":'
MAX_FRAME_BYTES = 8 * 1024 * 1024
PHOTO_SCHEMA = "desk_buddy.photo.v1"


@dataclass(frozen=True)
class BinaryFrame:
    metadata: dict
    jpeg: bytes


def encode_frame(metadata: Mapping, jpeg: bytes) -> bytes:
    _validate_jpeg(jpeg)
    body = dict(metadata)
    body.pop("payload", None)
    prefix = json.dumps(body, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if not prefix.endswith(b"}"):
        raise ValueError("invalid frame metadata")
    result = prefix[:-1] + PAYLOAD_MARKER + jpeg + b"}"
    if len(result) > MAX_FRAME_BYTES:
        raise ValueError("binary photo frame is larger than 8 MiB")
    return result


def decode_frame(payload: bytes) -> BinaryFrame:
    metadata, jpeg = decode_metadata(payload)
    _validate_jpeg(jpeg)
    return BinaryFrame(metadata, jpeg)


def validate_photo_frame(frame: BinaryFrame) -> None:
    """Validate metadata specific to an ESP32 photo publication."""
    _validate_jpeg(frame.jpeg)
    metadata = frame.metadata
    if (
        metadata.get("schema") != PHOTO_SCHEMA
        or metadata.get("sender") != "firmware"
        or metadata.get("content_type") != "image/jpeg"
        or metadata.get("photo") != "sending_photo"
    ):
        raise ValueError("photo metadata does not match the photo protocol")
    if metadata.get("size") != len(frame.jpeg):
        raise ValueError("photo metadata size does not match the JPEG")
    if _action_id(metadata.get("action_id")) == "":
        raise ValueError("photo metadata needs an action ID")
    if metadata.get("type") not in {
        "photo", "detect_object", "detect_color", "calibrate_depth",
    }:
        raise ValueError("photo metadata has an unsupported capture type")
    for field in ("width", "height"):
        value = metadata.get(field)
        if type(value) is not int or value <= 0:
            raise ValueError(f"photo metadata {field} must be a positive integer")


def _action_id(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    return str(value)


def decode_metadata(payload: bytes) -> tuple[dict, bytes]:
    """Parse the JSON prefix while leaving JPEG validation to the caller.

    This lets the service recover an action ID and publish a correlated
    terminal error when the framed image itself is damaged.
    """
    if not isinstance(payload, bytes) or len(payload) > MAX_FRAME_BYTES:
        raise ValueError("invalid or oversized binary photo frame")
    marker = payload.find(PAYLOAD_MARKER)
    if marker < 1 or not payload.endswith(b"}"):
        raise ValueError("binary photo payload marker is missing")
    try:
        metadata = json.loads(payload[:marker] + b"}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("binary photo metadata is not valid JSON") from exc
    if not isinstance(metadata, dict):
        raise ValueError("binary photo metadata must be an object")
    jpeg = payload[marker + len(PAYLOAD_MARKER):-1]
    return metadata, jpeg


def _validate_jpeg(jpeg: bytes) -> None:
    if not isinstance(jpeg, bytes) or len(jpeg) < 4:
        raise ValueError("photo payload is empty")
    if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
        raise ValueError("photo payload is not a complete JPEG")
