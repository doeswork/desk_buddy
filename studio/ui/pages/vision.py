"""Vision — the image interpreter. Studio installs models, never bundles them."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ..components import ActionSpec, Card, Column, Separator, SidePanel
from .base import Page


class VisionPage(Page):
    key = "vision"
    label = "Vision"

    title = "Image Interpreter"
    subtitle = "Install and run vetted models. Studio installs them, never bundles them."
    status = "No model running"

    def build_actions(self) -> list:
        return [
            ActionSpec("Install Model", primary=True),
            ActionSpec("Remove"),
            Separator(),
            ActionSpec("Start"),
            ActionSpec("Stop"),
            Separator(),
            ActionSpec("Test Photo"),
        ]

    def build_side(self) -> QWidget:
        return SidePanel("Models", [
            "YOLO — detect",
            "Google one-shot",
            "Depth — mono",
            "Browse catalog…",
        ])

    def build_page(self) -> QWidget:
        return Column(
            self.build_installed(),
            self.build_last_detection(),
        )

    def build_installed(self) -> QWidget:
        return Card("No models installed", "Pick one from the catalog to get started.")

    def build_last_detection(self) -> QWidget:
        return Card("Last detection", "Nothing yet.")
