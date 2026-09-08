"""Studio-facing re-export of detection normalization."""

from .worker_runtime.desk_buddy_vision_worker.contracts import (
    DETECTION_SCHEMA,
    normalize_batch,
)

__all__ = ["DETECTION_SCHEMA", "normalize_batch"]
