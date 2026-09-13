"""Visual: where the camera is, relative to the arm.

`calibrate_depth` is a photo action (MQTT_SPEC.md §6): the firmware publishes
the JPEG on the robot's photo topic and a truthful terminal result on its event
topic. This page triggers the capture and records successful publication.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ....services.network.pub_sub.calibration_messages import send_calibrate_depth
from ...theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING
from .step import StepPage


class VisualPage(StepPage):
    key = "visual"
    step_key = "visual"
    label = "3 · Visual"
    title = "Visual"
    subtitle = "Where the camera is, relative to the arm."

    def build_form(self) -> QWidget:
        return CaptureForm(enabled=self.can_send, on_capture=self._capture)

    def _capture(self) -> None:
        client = self.workspace.client()
        action_id = send_calibrate_depth(client, self.workspace.robot)
        self.send(action_id, waiting_text="Requesting a depth capture…")

    def on_completed(self, payload: dict) -> None:
        # No calibration values to save — the capture itself is the result.
        super().on_completed({"captured": True})


class CaptureForm(QWidget):
    def __init__(self, *, enabled: bool, on_capture) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, CARD_MARGIN_V
        )
        layout.setSpacing(CARD_SPACING * 2)

        row = QHBoxLayout()
        capture = QPushButton("Capture Depth Reference")
        capture.setObjectName("ToolbarPrimary")
        capture.setCursor(Qt.PointingHandCursor)
        capture.setEnabled(enabled)
        capture.clicked.connect(on_capture)
        row.addWidget(capture)
        row.addStretch(1)
        layout.addLayout(row)
