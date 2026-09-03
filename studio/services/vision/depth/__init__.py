"""Monocular-depth adapters and worker entry points."""

from __future__ import annotations

from .huggingface import HuggingFaceDepthHandler

__all__ = ["HuggingFaceDepthHandler"]
