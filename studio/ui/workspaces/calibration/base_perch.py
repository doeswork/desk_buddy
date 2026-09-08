"""Base + Perch: where the arm sits, and where it rests between moves.

Sends one `calibrate` request per perch value (MQTT_SPEC.md §5.1) and a
`calibrate_base_rotation` run for the base's counts-per-revolution profile
(§4.5). The profile run is the one long-running step in this workspace — it
publishes `status:"progress"` messages for minutes, which StepPage already
shows as they arrive.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....services.network.pub_sub.calibration_messages import (
    send_base_rotation_profile,
    send_perch_value,
)
from ...theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING
from .step import StepPage

FIELDS = (
    ("elbow", "Elbow angle (°)", 0, 180, 120),
    ("wrist", "Wrist angle (°)", 0, 180, 90),
    ("twist", "Twist angle (°)", 0, 180, 90),
    ("min", "Minimum reach (mm)", 0, 500, 0),
    ("mid", "Middle reach (mm)", 0, 500, 50),
    ("max", "Maximum reach (mm)", 0, 500, 100),
)


class BasePerchPage(StepPage):
    key = "base_perch"
    step_key = "base_perch"
    label = "1 · Base + Perch"
    title = "Base + Perch"
    subtitle = "Where the arm sits, and where it rests between moves."

    def build_form(self) -> QWidget:
        return PerchForm(
            enabled=self.can_send,
            on_send_value=self._send_value, on_run_profile=self._run_profile,
        )

    def _send_value(self, field: str, value: float) -> None:
        client = self.workspace.client()
        action_id = send_perch_value(client, self.workspace.robot, field, value)
        self.send(action_id, waiting_text=f"Setting {field}…")

    def _run_profile(self) -> None:
        client = self.workspace.client()
        action_id = send_base_rotation_profile(client, self.workspace.robot)
        self.send(action_id, waiting_text="Measuring base rotation — this takes a few minutes…")


class PerchForm(QWidget):
    def __init__(self, *, enabled: bool, on_send_value, on_run_profile) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, CARD_MARGIN_V
        )
        layout.setSpacing(CARD_SPACING * 2)

        form = QFormLayout()
        form.setSpacing(CARD_SPACING * 2)
        self._spins: dict[str, QDoubleSpinBox] = {}

        for key, field_label, low, high, default in FIELDS:
            spin = QDoubleSpinBox()
            spin.setRange(low, high)
            spin.setValue(default)
            self._spins[key] = spin

            row = QHBoxLayout()
            row.addWidget(spin, 1)
            send = QPushButton("Set")
            send.setObjectName("ToolbarAction")
            send.setCursor(Qt.PointingHandCursor)
            send.setEnabled(enabled)
            send.clicked.connect(
                lambda _checked=False, k=key: on_send_value(k, self._spins[k].value())
            )
            row.addWidget(send)
            form.addRow(field_label, row)

        layout.addLayout(form)

        layout.addSpacing(CARD_SPACING * 3)
        profile_row = QHBoxLayout()
        profile = QPushButton("Run Base Rotation Profile")
        profile.setObjectName("ToolbarPrimary")
        profile.setCursor(Qt.PointingHandCursor)
        profile.setEnabled(enabled)
        profile.clicked.connect(on_run_profile)
        profile_row.addWidget(profile)
        profile_row.addStretch(1)
        layout.addLayout(profile_row)
