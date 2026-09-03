"""Compatibility codecs for the existing ESP32/calibration-tool MQTT flow."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

@dataclass(frozen=True)
class FirmwarePhoto:
    metadata: dict[str, Any]
    jpeg: bytes


def decode_firmware_photo(payload: bytes) -> FirmwarePhoto | None:
    marker = payload.find(b'"payload"')
    colon = payload.find(b":", marker + len(b'"payload"')) if marker >= 0 else -1
    jpeg_start = payload.find(b"\xff\xd8", colon + 1) if colon >= 0 else -1
    jpeg_end = payload.rfind(b"\xff\xd9")
    if marker < 0 or colon < 0 or jpeg_start < 0 or jpeg_end < jpeg_start:
        return None
    try:
        metadata = json.loads(payload[: colon + 1] + b"null" + payload[jpeg_end + 2 :])
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    jpeg = payload[jpeg_start : jpeg_end + 2]
    if not isinstance(metadata, dict) or not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
        return None
    return FirmwarePhoto(metadata, jpeg)


def legacy_request(body: Mapping[str, Any]) -> dict[str, Any] | None:
    action = str(body.get("action") or "")
    if action not in {"detect_object", "calibrate_depth", "photo"}:
        return None
    return {
        "action": action,
        "action_id": str(body.get("action_id") or ""),
        "prompt": str(body.get("phrase") or ""),
        "requested_by": str(body.get("sender") or ""),
        "execute": action == "detect_object" and bool(body.get("execute", body.get("use_model", False))),
        "model_id": str(body.get("model_name") or ""),
        "box_threshold": float(body.get("box_threshold", 0.25)),
        "text_threshold": float(body.get("text_threshold", 0.2)),
    }


def legacy_progress(
    action_id: str, stage: str, message: str, *, operation_type: str = "detect_object", **values: Any
) -> dict[str, Any]:
    return {
        "sender": "visual_ai",
        "action_id": action_id,
        "status": "in_progress",
        "type": operation_type,
        "stage": stage,
        "log": message,
        **values,
    }


def legacy_terminal(
    action_id: str,
    *,
    success: bool,
    stage: str,
    error: str | None = None,
    operation_type: str = "detect_object",
    **values: Any,
) -> dict[str, Any]:
    result = {
        "sender": "visual_ai",
        "action_id": action_id,
        "status": "completed" if success else "failed",
        "type": operation_type,
        "stage": stage,
        **values,
    }
    if error:
        result["error"] = error
    return result
