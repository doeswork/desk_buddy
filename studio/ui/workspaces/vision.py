"""Vision — the image interpreter. Studio installs models, never bundles them.

One page for now. Models and Detections will split as they grow.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ..components import ActionSpec, Card, Column, Separator
from ..pages.base import Page
from .base import Workspace


class ModelsPage(Page):
    key = "models"
    label = "Models"

    title = "Image Interpreter"
    subtitle = "Install and run vetted models. Studio installs them, never bundles them."
    status = "No model running"

    def build_page(self) -> QWidget:
        return Column(
            self.build_installed(),
            self.build_last_detection(),
        )

    def build_installed(self) -> QWidget:
        return Card("No models installed", "Pick one from the catalog to get started.")

    def build_last_detection(self) -> QWidget:
        return Card("Last detection", "Nothing yet.")


class VisionWorkspace(Workspace):
    key = "vision"
    label = "Vision"
    page_classes = [ModelsPage]

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
