"""Calibration — Base + Perch, IK, Visual, Reach and Grab, Stencil."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ..components import ActionSpec, Card, Column, Separator, SidePanel
from .base import Page


class CalibrationPage(Page):
    key = "calibration"
    label = "Calibration"

    title = "Calibration"
    subtitle = "Base + Perch, IK, Visual, Reach and Grab, Stencil."
    status = "No robot connected"

    def build_actions(self) -> list:
        return [
            ActionSpec("Start Step", primary=True),
            ActionSpec("Skip"),
            ActionSpec("Reset"),
            Separator(),
            ActionSpec("Save to Robot"),
        ]

    def build_side(self) -> QWidget:
        return SidePanel("Steps", [
            "1 · Base + Perch",
            "2 · IK",
            "3 · Visual",
            "4 · Reach and Grab",
            "5 · Stencil",
        ])

    def build_page(self) -> QWidget:
        return Column(
            self.build_progress(),
            self.build_saved_values(),
        )

    def build_progress(self) -> QWidget:
        return Card("Not calibrated", "Connect a robot to begin.")

    def build_saved_values(self) -> QWidget:
        return Card("Saved values", "Nothing stored.")
