"""Inverse Kinematics: joint lengths and limits, so a target position becomes
angles.

Sends one `calibrate` request per hover point (MQTT_SPEC.md §5.1). The three
z=0 points (`hover_over_min/mid/max`) are required for basic motion; the
z=50 points (`hover_min_120/mid_120/max_120`) are optional and only needed
for non-zero-height reach.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....services.network.pub_sub.calibration_messages import send_hover_point
from ...theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING
from .step import StepPage

POINTS = (
    ("Z = 0 mm", (
        ("hover_over_min", "Min"),
        ("hover_over_mid", "Mid"),
        ("hover_over_max", "Max"),
    )),
    ("Z = 50 mm (optional)", (
        ("hover_min_120", "Min"),
        ("hover_mid_120", "Mid"),
        ("hover_max_120", "Max"),
    )),
)


class IKPage(StepPage):
    key = "ik"
    step_key = "ik"
    label = "2 · IK"
    title = "Inverse Kinematics"
    subtitle = "Joint lengths and limits, so a target position becomes angles."

    def build_form(self) -> QWidget:
        return HoverForm(enabled=self.can_send, on_send=self._send)

    def _send(self, calibration_type: str, distance: float,
               elbow: float, wrist: float, twist: float) -> None:
        client = self.workspace.client()
        action_id = send_hover_point(
            client, self.workspace.robot, calibration_type, distance,
            elbow=elbow, wrist=wrist, twist=twist,
        )
        self.send(action_id, waiting_text=f"Setting {calibration_type}…")


class HoverForm(QWidget):
    def __init__(self, *, enabled: bool, on_send) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, CARD_MARGIN_V
        )
        layout.setSpacing(CARD_SPACING * 3)

        for group_label, points in POINTS:
            group = QGroupBox(group_label)
            form = QFormLayout(group)
            form.setSpacing(CARD_SPACING * 2)

            for calibration_type, point_label in points:
                label, widget = self._point_row(calibration_type, point_label, enabled, on_send)
                form.addRow(label, widget)

            layout.addWidget(group)

    @staticmethod
    def _point_row(calibration_type: str, point_label: str, enabled: bool, on_send) -> tuple[str, QWidget]:
        row = QHBoxLayout()
        holder = QWidget()
        holder.setLayout(row)

        fields = {}
        for name, label_text, low, high, default in (
            ("distance", "Distance (mm)", 0, 500, 60),
            ("elbow", "Elbow (°)", 0, 180, 90),
            ("wrist", "Wrist (°)", 0, 180, 90),
            ("twist", "Twist (°)", 0, 180, 90),
        ):
            spin = QDoubleSpinBox()
            spin.setRange(low, high)
            spin.setValue(default)
            spin.setPrefix(f"{label_text}: ")
            fields[name] = spin
            row.addWidget(spin)

        send = QPushButton("Set")
        send.setObjectName("ContextAction")
        send.setCursor(Qt.PointingHandCursor)
        send.setEnabled(enabled)
        send.clicked.connect(
            lambda: on_send(
                calibration_type,
                fields["distance"].value(),
                fields["elbow"].value(),
                fields["wrist"].value(),
                fields["twist"].value(),
            )
        )
        row.addWidget(send)

        return point_label, holder
