"""Stencil: the working area the arm may move within.

`stencilCalibrate` (MQTT_SPEC.md §5.3) is the one genuinely multi-step
command in the calibration set: START, then RUN_POINT/ADJUST across 15
points, then the firmware itself saves rot_off_deg/ik_off_mm/st_map at
completion. This page follows the firmware's own session shape — driven by
the nested `stencil_calibration.phase` and `pointIndex` in each reply —
rather than inventing a different one.

Points are not sent individually from here: RUN_POINT always acts on
whichever point the firmware's RAM session is currently on, so this page is a
narrow remote control (Start / Run Point / Adjust / Cancel) over that state,
not a form with 15 rows of its own.
"""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....models.config.calibrations import Calibration, calibrations
from ....services.network.pub_sub.calibration_messages import send_stencil_command
from ...components import Card
from ...theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING
from .step import StepPage

# Reached when the firmware's session has produced the final stencil result —
# see §5.3: "At final completion, the firmware saves rot_off_deg / ik_off_mm
# / st_map." Every other STATUS/RUN_POINT/ADJUST reply is progress, not a
# result to write to Calibrations.
FINAL_PHASE = "complete"


class StencilPage(StepPage):
    key = "stencil"
    step_key = "stencil"
    label = "5 · Stencil"
    title = "Stencil"
    subtitle = "The working area the arm may move within."

    def __init__(self, workspace=None) -> None:
        super().__init__(workspace)
        self._session: dict = {}

    def build_form(self) -> QWidget:
        return StencilForm(
            self._session,
            enabled=self.can_send,
            on_start=lambda: self._command("START"),
            on_run_point=lambda: self._command("RUN_POINT"),
            on_status=lambda: self._command("STATUS"),
            on_cancel=lambda: self._command("CANCEL"),
            on_adjust=self._adjust,
        )

    def _command(self, command: str) -> None:
        client = self.workspace.client()
        action_id = send_stencil_command(client, self.workspace.robot, command)
        self.send(action_id, waiting_text=f"{command.title()}…")

    def _adjust(self, rotation_nudge: float, distance_nudge: float) -> None:
        client = self.workspace.client()
        action_id = send_stencil_command(
            client, self.workspace.robot, "ADJUST",
            rotation_nudge_degrees=rotation_nudge, distance_nudge_mm=distance_nudge,
        )
        self.send(action_id, waiting_text="Adjusting…")

    # Every stencilCalibrate reply is progress on the same session, not a
    # one-shot completed/failed exchange — so any reply that carries the
    # session detail updates the page, and only the firmware's own final
    # phase is written to Calibrations.
    def is_terminal(self, payload: dict) -> bool:
        return "stencil_calibration" in payload

    def on_completed(self, payload: dict) -> None:
        self._session = payload.get("stencil_calibration", {})
        if self._session.get("phase") != FINAL_PHASE:
            return

        robot = self.workspace.robot
        calibrations().save(Calibration(
            robot=robot,
            step=self.step_key,
            values={
                "rot_off_deg": self._session.get("savedRotationOffsetDegrees"),
                "ik_off_mm": self._session.get("savedIkOffsetMm"),
            },
            saved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ))


class StencilForm(QWidget):
    def __init__(self, session: dict, *, enabled: bool, on_start, on_run_point,
                 on_status, on_cancel, on_adjust) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, CARD_MARGIN_V
        )
        layout.setSpacing(CARD_SPACING * 2)

        layout.addWidget(self._session_card(session))

        buttons = QHBoxLayout()
        buttons.setSpacing(CARD_SPACING * 2)

        start = QPushButton("Start")
        start.setObjectName("ToolbarPrimary")
        start.setCursor(Qt.PointingHandCursor)
        start.setEnabled(enabled)
        start.clicked.connect(on_start)
        buttons.addWidget(start)

        run_point = QPushButton("Run Point")
        run_point.setObjectName("ToolbarAction")
        run_point.setCursor(Qt.PointingHandCursor)
        run_point.setEnabled(enabled)
        run_point.clicked.connect(on_run_point)
        buttons.addWidget(run_point)

        status = QPushButton("Status")
        status.setObjectName("ToolbarAction")
        status.setCursor(Qt.PointingHandCursor)
        status.setEnabled(enabled)
        status.clicked.connect(on_status)
        buttons.addWidget(status)

        cancel = QPushButton("Cancel")
        cancel.setObjectName("ToolbarAction")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setEnabled(enabled)
        cancel.clicked.connect(on_cancel)
        buttons.addWidget(cancel)

        buttons.addStretch(1)
        layout.addLayout(buttons)

        layout.addSpacing(CARD_SPACING * 3)
        layout.addWidget(self._adjust_row(enabled, on_adjust))

    @staticmethod
    def _session_card(session: dict) -> QWidget:
        if not session:
            return Card("No active session", "Start to begin the 15-point stencil walk.")
        phase = session.get("phase", "?")
        point = session.get("pointIndex", "?")
        return Card(f"Phase: {phase}", f"Point {point} of 15")

    @staticmethod
    def _adjust_row(enabled: bool, on_adjust) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)

        label = QLabel("Adjust:")
        label.setObjectName("CardBody")
        layout.addWidget(label)

        rotation = QDoubleSpinBox()
        rotation.setRange(-45, 45)
        rotation.setPrefix("Rotation °: ")
        layout.addWidget(rotation)

        distance = QDoubleSpinBox()
        distance.setRange(-50, 50)
        distance.setPrefix("Distance mm: ")
        layout.addWidget(distance)

        apply_button = QPushButton("Apply")
        apply_button.setObjectName("ToolbarAction")
        apply_button.setCursor(Qt.PointingHandCursor)
        apply_button.setEnabled(enabled)
        apply_button.clicked.connect(
            lambda: on_adjust(rotation.value(), distance.value())
        )
        layout.addWidget(apply_button)
        layout.addStretch(1)
        return holder
