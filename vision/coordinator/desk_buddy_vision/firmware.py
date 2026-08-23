from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


def looks_like_jpeg(payload: bytes) -> bool:
    raw = bytes(payload)
    return len(raw) >= 4 and raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9")


def try_parse_json(payload: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(bytes(payload).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def extract_mixed_json_jpeg(payload: bytes) -> tuple[dict[str, Any] | None, bytes] | None:
    """Parse the ESP32's legacy JSON-prefix plus raw-JPEG payload."""
    raw = bytes(payload)
    marker_index = raw.find(b'"payload"')
    if marker_index < 0:
        return None
    colon_index = raw.find(b":", marker_index + len(b'"payload"'))
    jpeg_start = raw.find(b"\xff\xd8", colon_index + 1) if colon_index >= 0 else -1
    jpeg_end = raw.rfind(b"\xff\xd9")
    if colon_index < 0 or jpeg_start < 0 or jpeg_end < jpeg_start:
        return None
    jpeg = raw[jpeg_start : jpeg_end + 2]
    json_candidate = raw[: colon_index + 1] + b" null " + raw[jpeg_end + 2 :]
    return try_parse_json(json_candidate), jpeg


@dataclass(frozen=True)
class FirmwarePayload:
    body: dict[str, Any] | None
    jpeg: bytes | None

    @property
    def action_id(self) -> str | None:
        if not self.body or self.body.get("action_id") is None:
            return None
        return str(self.body["action_id"])

    @property
    def is_visual_ai_echo(self) -> bool:
        return bool(self.body and str(self.body.get("sender") or "").lower() in {"visual_ai", "vision_coordinator"})


def parse_firmware_payload(payload: bytes) -> FirmwarePayload:
    raw = bytes(payload)
    body = try_parse_json(raw)
    if body is not None:
        return FirmwarePayload(body=body, jpeg=None)
    if looks_like_jpeg(raw):
        return FirmwarePayload(body=None, jpeg=raw)
    mixed = extract_mixed_json_jpeg(raw)
    if mixed is not None:
        mixed_body, jpeg = mixed
        if not looks_like_jpeg(jpeg):
            raise ValueError("embedded JPEG has invalid SOI or EOI markers")
        return FirmwarePayload(body=mixed_body, jpeg=jpeg)
    raise ValueError("firmware payload is neither JSON nor a valid JPEG message")


def firmware_photo_command(*, action: str, action_id: str, sender: str = "ai_server", **fields: Any) -> dict[str, Any]:
    if action not in {"photo", "detect_object", "calibrate_depth"}:
        raise ValueError(f"unsupported photo action: {action}")
    result: dict[str, Any] = {"sender": sender, "action_id": action_id, "action": action}
    result.update(fields)
    return result


def firmware_status_is_for(body: Mapping[str, Any], action_id: str) -> bool:
    return (
        str(body.get("sender") or "").lower() == "firmware"
        and str(body.get("action_id") or "") == action_id
        and str(body.get("status") or "").lower() in {"in_progress", "completed", "failed"}
    )

