"""Manual Controller — direct drive: servos, base, gripper, camera."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ..components import ActionSpec, Card, Column, Separator, SidePanel
from .base import Page


class ManualPage(Page):
    key = "manual"
    label = "Manual"

    title = "Manual Controller"
    subtitle = "Direct drive: servos, base, gripper, camera."
    status = "No robot connected"

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

    def build_side(self) -> QWidget:
        return SidePanel("Joints", ["Base", "Shoulder", "Elbow", "Wrist", "Gripper"])

    def build_page(self) -> QWidget:
        return Column(
            self.build_telemetry(),
            self.build_camera(),
        )

    def build_telemetry(self) -> QWidget:
        return Card("No telemetry", "Connect a robot to see live joint positions.")

    def build_camera(self) -> QWidget:
        return Card("Camera", "No image.")
