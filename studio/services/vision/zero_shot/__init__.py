"""Zero-shot detector adapters and worker entry points."""

from __future__ import annotations

from .huggingface import HuggingFaceZeroShotHandler

__all__ = ["HuggingFaceZeroShotHandler"]
