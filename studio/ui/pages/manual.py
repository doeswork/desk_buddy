"""Manual Controller — direct drive: servos, base, gripper, camera."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

try:
    from ..widgets import card
    from .base import SEPARATOR, Page, stack
except ImportError:
    from widgets import card
    from base import SEPARATOR, Page, stack


class ManualPage(Page):
    key = "manual"
    label = "Manual"

    title = "Manual Controller"
    subtitle = "Direct drive: servos, base, gripper, camera."
    status = "No robot connected"

    actions = ["Home", "Perch", SEPARATOR, "Open", "Close", SEPARATOR, "Photo"]

    side_title = "Joints"
    side_items = ["Base", "Shoulder", "Elbow", "Wrist", "Gripper"]

    def build_page(self) -> QWidget:
        return stack(
            card("No telemetry", "Connect a robot to see live joint positions."),
            card("Camera", "No image."),
        )
