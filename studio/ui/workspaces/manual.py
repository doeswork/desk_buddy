"""Manual Controller — direct drive: servos, base, gripper, camera."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ..components import ActionSpec, Card, Column, Separator
from ..pages.base import Page
from .base import Workspace


class DrivePage(Page):
    key = "drive"
    label = "Drive"

    title = "Manual Controller"
    subtitle = "Direct drive: servos, base, gripper, camera."
    status = "No robot connected"

    def build_page(self) -> QWidget:
        return Column(
            self.build_telemetry(),
            self.build_camera(),
        )

    def build_telemetry(self) -> QWidget:
        return Card("No telemetry", "Connect a robot to see live joint positions.")

    def build_camera(self) -> QWidget:
        return Card("Camera", "No image.")


class ManualWorkspace(Workspace):
    key = "manual"
    label = "Manual"
    page_classes = [DrivePage]

    def build_actions(self) -> list:
        return [
            ActionSpec("Home", primary=True),
            ActionSpec("Perch"),
            Separator(),
            ActionSpec("Open"),
            ActionSpec("Close"),
            Separator(),
            ActionSpec("Photo"),
        ]
