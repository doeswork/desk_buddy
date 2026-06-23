from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any, Dict, Optional


PHOTO_MARKER = b',"payload":'


@dataclass
class DecodedPhoto:
    metadata: Dict[str, Any]
    jpeg_bytes: bytes

    @property
    def action_id(self) -> str:
        return str(self.metadata.get("action_id", ""))


def decode_photo_message(raw: bytes) -> Optional[DecodedPhoto]:
    """Decode the firmware's streamed photo envelope.

    Firmware publishes a JSON-ish prefix, then raw JPEG bytes, then a final
    closing brace. The JPEG payload is intentionally not base64, so normal JSON
    parsing cannot be used for the whole message.
    """
    marker_index = raw.find(PHOTO_MARKER)
    if marker_index < 0 or not raw.endswith(b"}"):
        return None

    prefix = raw[:marker_index] + b"}"
    jpeg = raw[marker_index + len(PHOTO_MARKER):-1]
    if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
        return None

    try:
        metadata = json.loads(prefix.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

    if metadata.get("sender") != "firmware" or metadata.get("photo") != "sending_photo":
        return None

    return DecodedPhoto(metadata=metadata, jpeg_bytes=jpeg)


def save_photo(photo: DecodedPhoto, directory: Path, label: str = "photo") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    safe_label = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in label).strip("_")
    safe_label = safe_label or "photo"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    action_id = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in photo.action_id)
    suffix = f"_{action_id}" if action_id else ""
    path = directory / f"{stamp}_{safe_label}{suffix}.jpg"
    path.write_bytes(photo.jpeg_bytes)
    return path

