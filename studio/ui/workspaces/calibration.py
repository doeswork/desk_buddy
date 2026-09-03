"""Calibration — Base + Perch, IK, Visual, Reach and Grab, Stencil.

Each step is a page. They run in order, and the side panel is the sequence,
so where you are in the process is where you are in the app.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ..components import ActionSpec, Card, Column, Separator
from ..pages.base import Page
from .base import Workspace


class StepPage(Page):
    """One calibration step. Subclasses supply the identity and the body."""

    status = "No robot connected"

    def build_page(self) -> QWidget:
        return Column(
            self.build_progress(),
            self.build_saved_values(),
        )

    def build_progress(self) -> QWidget:
        return Card("Not calibrated", "Connect a robot to begin.")

    def build_saved_values(self) -> QWidget:
        return Card("Saved values", "Nothing stored.")


class BasePerchPage(StepPage):
    key = "base_perch"
    label = "1 · Base + Perch"
    title = "Base + Perch"
    subtitle = "Where the arm sits, and where it rests between moves."


class IKPage(StepPage):
    key = "ik"
    label = "2 · IK"
    title = "Inverse Kinematics"
    subtitle = "Joint lengths and limits, so a target position becomes angles."


class VisualPage(StepPage):
    key = "visual"
    label = "3 · Visual"
    title = "Visual"
    subtitle = "Where the camera is, relative to the arm."


class ReachGrabPage(StepPage):
    key = "reach_grab"
    label = "4 · Reach and Grab"
    title = "Reach and Grab"
    subtitle = "Approach distance and grip force for picking things up."


class StencilPage(StepPage):
    key = "stencil"
    label = "5 · Stencil"
    title = "Stencil"
    subtitle = "The working area the arm may move within."


class CalibrationWorkspace(Workspace):
    key = "calibration"
    label = "Calibration"

    page_classes = [
        BasePerchPage,
        IKPage,
        VisualPage,
        ReachGrabPage,
        StencilPage,
    ]

    def build_actions(self) -> list:
        return [
            ActionSpec("Start Step", primary=True),
            ActionSpec("Skip"),
            ActionSpec("Reset"),
            Separator(),
            ActionSpec("Save to Robot"),
        ]
