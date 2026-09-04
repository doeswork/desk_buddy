"""StepPage: what every calibration step has in common.

A step publishes one or more MQTT commands to the selected robot, waits for
the firmware's reply, and — on success — records the result in Calibrations.
That lifecycle (waiting text, success, failure, "no robot selected") is the
same shape on every step; only the form fields and which calibration_type
they send differ, so it lives here once rather than five times.
"""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from ....models.config.calibrations import Calibration, calibrations
from ...components import Card, Column
from ...pages.base import Page
from ...theme.metrics import CARD_GAP, ROW_PADDING


class StepPage(Page):
    """One calibration step. Subclasses supply the form and the send logic."""

    # Set by subclasses: the key under STEP_KEYS this page writes results for.
    step_key = ""

    def __init__(self, workspace=None) -> None:
        super().__init__(workspace)
        # "" when idle, else the action_id currently awaiting a reply — used
        # to ignore stale replies from a request the user has since replaced.
        self._pending = ""
        self._waiting_text = ""
        self._error = ""
        self._unsubscribe = None

    @property
    def status(self) -> str:
        robot = self.workspace.robot
        if not robot:
            return "No robot selected"
        if self._pending:
            return f"{robot} — waiting for a reply"
        saved = calibrations().find(robot, self.step_key)
        return f"{robot} — saved" if saved else f"{robot} — not calibrated"

    # ---- body ------------------------------------------------------------
    def build_page(self) -> QWidget:
        sections = [self.build_robot_picker()]

        robot = self.workspace.robot
        if not robot:
            sections.append(Card(
                "No robot marked yet",
                "Mark a user as a robot on Network → Robots to enable "
                "the form below.",
            ))

        if self._pending:
            sections.append(Card("Waiting for the robot…", self._waiting_text))
        else:
            if self._error:
                sections.append(Card("The robot reported a problem", self._error))
            # Shown either way, so a step's fields are always visible to read
            # and fill in — only sending is gated on a robot being selected.
            sections.append(self.build_form())

        sections.append(self.build_saved_values())
        return Column(*sections)

    @property
    def can_send(self) -> bool:
        """Whether the form's buttons should be clickable right now."""
        return bool(self.workspace.robot) and not self._pending

    def build_robot_picker(self) -> QWidget:
        available = self.workspace.robots()
        if len(available) < 2:
            return QWidget()

        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, CARD_GAP)
        layout.setSpacing(ROW_PADDING)

        label = QLabel("Robot")
        label.setObjectName("CardBody")
        layout.addWidget(label)

        combo = QComboBox()
        names = [robot.name for robot in available]
        labels = [robot.display_name for robot in available]
        combo.addItems(labels)
        current = self.workspace.robot
        if current in names:
            combo.setCurrentIndex(names.index(current))
        combo.currentIndexChanged.connect(
            lambda index: self.workspace.select_robot(names[index])
        )
        layout.addWidget(combo, 1)
        return holder

    def build_form(self) -> QWidget:
        """The step's own fields and send button. Override in every step."""
        raise NotImplementedError

    def build_saved_values(self) -> QWidget:
        robot = self.workspace.robot
        saved = calibrations().find(robot, self.step_key) if robot else None
        if saved is None:
            return Card("Saved values", "Nothing stored.")

        lines = "\n".join(f"{key}: {value}" for key, value in saved.values.items())
        return Card(f"Saved values — {saved.saved_at}", lines or "Nothing stored.")

    # ---- sending a command and waiting for its reply ----------------------
    def send(self, action_id: str, *, waiting_text: str = "Sending…") -> None:
        """Start waiting on one action_id. The workspace's client delivers the
        matching reply to `_on_reply` on the robot's own reply topic."""
        self._cancel_wait()
        self._pending = action_id
        self._waiting_text = waiting_text
        self._error = ""

        robot = self.workspace.robot
        topic = f"{robot}/test"
        self._unsubscribe = self.workspace.client().subscribe(topic, self._on_reply)
        self.rebuild()

    def _on_reply(self, _topic: str, payload: dict) -> None:
        if payload.get("action_id") != self._pending:
            return
        status = payload.get("status")
        if status == "progress":
            self._waiting_text = str(payload.get("progress", payload))
            self.rebuild()
            return
        if status == "failed":
            self._cancel_wait()
            self._error = str(payload.get("error") or payload.get("message") or "failed")
            self.rebuild()
            return
        if not self.is_terminal(payload):
            return

        self._cancel_wait()
        self.on_completed(payload)
        self.rebuild()

    def is_terminal(self, payload: dict) -> bool:
        """Whether this reply is the one to treat as success. Default: the
        ordinary `calibrate` terminal status. A photo action (§6) has no
        `completed` reply — override for its in_progress/log:"sent" shape."""
        return payload.get("status") == "completed"

    def on_completed(self, payload: dict) -> None:
        """A terminal `completed` reply arrived. Default: save the whole
        detail payload verbatim. Override for a step whose result needs
        picking specific keys out of a nested object."""
        robot = self.workspace.robot
        calibrations().save(Calibration(
            robot=robot,
            step=self.step_key,
            values={k: v for k, v in payload.items()
                    if k not in ("sender", "action_id", "status")},
            saved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ))

    def _cancel_wait(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._pending = ""
