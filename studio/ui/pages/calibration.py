"""Calibration — Base + Perch, IK, Visual, Reach and Grab, Stencil."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

try:
    from ..widgets import card
    from .base import SEPARATOR, Page, stack
except ImportError:
    from widgets import card
    from base import SEPARATOR, Page, stack


class CalibrationPage(Page):
    key = "calibration"
    label = "Calibration"

    title = "Calibration"
    subtitle = "Base + Perch, IK, Visual, Reach and Grab, Stencil."
    status = "No robot connected"

    actions = ["Start Step", "Skip", "Reset", SEPARATOR, "Save to Robot"]

    side_title = "Steps"
    side_items = [
        "1 · Base + Perch",
        "2 · IK",
        "3 · Visual",
        "4 · Reach and Grab",
        "5 · Stencil",
    ]

    def build_page(self) -> QWidget:
        return stack(
            card("Not calibrated", "Connect a robot to begin."),
            card("Saved values", "Nothing stored."),
        )
