"""Vision — the image interpreter. Studio installs models, never bundles them."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

try:
    from ..widgets import card
    from .base import SEPARATOR, Page, stack
except ImportError:
    from widgets import card
    from base import SEPARATOR, Page, stack


class VisionPage(Page):
    key = "vision"
    label = "Vision"

    title = "Image Interpreter"
    subtitle = "Install and run vetted models. Studio installs them, never bundles them."
    status = "No model running"

    actions = [
        "Install Model", "Remove", SEPARATOR,
        "Start", "Stop", SEPARATOR,
        "Test Photo",
    ]

    side_title = "Models"
    side_items = ["YOLO — detect", "Google one-shot", "Depth — mono", "Browse catalog…"]

    def build_page(self) -> QWidget:
        return stack(
            card("No models installed", "Pick one from the catalog to get started."),
            card("Last detection", "Nothing yet."),
        )
