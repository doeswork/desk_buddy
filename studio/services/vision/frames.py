"""Studio-facing re-export of the worker's binary frame contract."""

from .worker_runtime.desk_buddy_vision_worker.frames import (
    BinaryFrame,
    MAX_FRAME_BYTES,
    PAYLOAD_MARKER,
    decode_frame,
    encode_frame,
)

__all__ = [
    "BinaryFrame", "MAX_FRAME_BYTES", "PAYLOAD_MARKER", "decode_frame", "encode_frame",
]
