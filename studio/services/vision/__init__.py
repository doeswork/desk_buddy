"""Managed zero-shot vision services.

The Studio-facing lifecycle code lives in this package.  The worker itself is
kept in ``worker_runtime`` so it can also be packaged as a tiny zipapp and run
inside the isolated Torch environment without importing Qt.
"""

from __future__ import annotations

from .catalog import BUILTIN_MODELS, DEFAULT_MODEL_ID, ModelManifest, model_manifest
from .manager import VisionServiceManager, VisionState

__all__ = [
    "BUILTIN_MODELS",
    "DEFAULT_MODEL_ID",
    "ModelManifest",
    "VisionServiceManager",
    "VisionState",
    "model_manifest",
]
