"""Reach and Grab: approach distance and grip force for picking things up.

`detect_object` is a photo action (MQTT_SPEC.md §6), like Visual's
`calibrate_depth` — same reply shape, same scope for this pass: trigger the
capture and confirm it was requested, without decoding the binary frame or
driving the external reach-and-grab orchestration described in §6's "External
reach-and-grab orchestration" (that is a Vision-server flow to monitor, not
one for this page to replay).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ....services.network.pub_sub.calibration_messages import send_detect_object
from ...theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING
from .step import StepPage


class ReachGrabPage(StepPage):
    key = "reach_grab"
    step_key = "reach_grab"
    label = "4 · Reach and Grab"
    title = "Reach and Grab"
    subtitle = "Approach distance and grip force for picking things up."

    def __init__(self, workspace=None) -> None:
        super().__init__(workspace)
        self._phrase = ""

    def build_form(self) -> QWidget:
        return DetectForm(
            self._phrase, enabled=self.can_send,
            on_phrase_changed=self._phrase_changed, on_detect=self._detect,
        )

    def _phrase_changed(self, text: str) -> None:
        self._phrase = text

    def _detect(self) -> None:
        client = self.workspace.client()
        phrase = self._phrase.strip() or None
        action_id = send_detect_object(client, self.workspace.robot, phrase=phrase)
        self.send(action_id, waiting_text="Requesting a detection capture…")

    def is_terminal(self, payload: dict) -> bool:
        return payload.get("status") == "in_progress" and payload.get("log") == "sent"

    def on_completed(self, payload: dict) -> None:
        super().on_completed({"captured": True, "phrase": self._phrase})


class DetectForm(QWidget):
    def __init__(self, phrase: str, *, enabled: bool, on_phrase_changed, on_detect) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, CARD_MARGIN_V
        )
        layout.setSpacing(CARD_SPACING * 2)

        row = QHBoxLayout()
        field = QLineEdit(phrase)
        field.setPlaceholderText("What to look for (optional)")
        field.textChanged.connect(on_phrase_changed)
        row.addWidget(field, 1)

        detect = QPushButton("Capture and Detect")
        detect.setObjectName("ToolbarPrimary")
        detect.setCursor(Qt.PointingHandCursor)
        detect.setEnabled(enabled)
        detect.clicked.connect(on_detect)
        row.addWidget(detect)
        layout.addLayout(row)
