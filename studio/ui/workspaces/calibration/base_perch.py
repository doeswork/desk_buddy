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
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....services.network.pub_sub.calibration_messages import (
    send_base_rotation_profile,
    send_perch_value,
)
from ...theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING
from .controls import PinnedSlider, ScrollSafeSpinBox, widest
from .step import StepPage

# The perch angles and the reach marks used to be here, as six typed fields.
# They belong to the camera, not to the base: a perch is the pose every photo
# is taken from, and the marks are what that view is measured against. Setting
# either one blind, on a page with no picture, is guesswork — so both moved to
# the Visual step, where the frame they produce is on screen beside them.
#
# What is left is what this page was always for: the base's own rotation
# profile, which is a property of the turntable and needs no camera at all.
ANGLE, DISTANCE = "angle", "distance"

FIELDS = ()


class BasePerchPage(StepPage):
    key = "base_perch"
    step_key = "base_perch"
    label = "1 · Base"
    title = "Base"
    subtitle = (
        "How far the turntable turns. The perch the camera looks from is "
        "step 3."
    )

    def build_form(self) -> QWidget:
        # Built once and kept — StepPage holds it across rebuilds, and
        # `PerchForm.set_enabled` is what it calls to keep it current. The
        # six values in here are dialled in by hand and must outlive a reply.
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
        # Both are scroll-safe: every one of these writes to the arm, and the
        # wheel belongs to the page that scrolls past them.
        self._inputs: dict[str, object] = {}
        self._readouts: dict[str, QLabel] = {}
        # Kept so the page can enable and disable them in place: rebuilding
        # this form to grey a button would discard the values in it.
        self._buttons: list[QPushButton] = []

        for key, field_label, low, high, default, kind in FIELDS:
            row = QHBoxLayout()
            row.setSpacing(CARD_SPACING)

            if kind is ANGLE:
                control = PinnedSlider(int(low), int(high), int(default))
                control.setEnabled(enabled)
                # Released, not per-pixel: a servo command per pixel of drag
                # would flood the broker and make the arm chase the hand.
                control.committed.connect(
                    lambda value, k=key: on_send_value(k, float(value))
                )
                row.addWidget(control, 1)

                # A slider says roughly; the number beside it says exactly.
                readout = QLabel(f"{default:g}°")
                readout.setObjectName("CardBody")
                readout.setMinimumWidth(widest(readout, "180°"))
                control.valueChanged.connect(
                    lambda value, r=readout: r.setText(f"{value:g}°")
                )
                row.addWidget(readout)
                self._readouts[key] = readout
            else:
                control = ScrollSafeSpinBox()
                control.setRange(low, high)
                control.setValue(default)
                row.addWidget(control, 1)

            self._inputs[key] = control

            send = QPushButton("Set")
            send.setObjectName("ToolbarAction")
            send.setCursor(Qt.PointingHandCursor)
            send.setEnabled(enabled)
            self._buttons.append(send)
            send.clicked.connect(
                lambda _checked=False, k=key: on_send_value(k, self._value(k))
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
        self._buttons.append(profile)
        profile_row.addWidget(profile)
        profile_row.addStretch(1)
        layout.addLayout(profile_row)

    def _value(self, key: str) -> float:
        """What this field currently holds, whichever control it uses."""
        return float(self._inputs[key].value())

    def set_enabled(self, enabled: bool) -> None:
        """Lock or unlock every control, keeping the values already entered.

        The page calls this instead of rebuilding while a command is out —
        the six values here were dialled in by hand, and rebuilding to grey a
        button would throw all of them away.
        """
        for control in self._inputs.values():
            control.setEnabled(enabled)
        for button in self._buttons:
            button.setEnabled(enabled)
