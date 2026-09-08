"""Inverse Kinematics: joint lengths and limits, so a target position becomes
angles.

Sends one `calibrate` request per hover point (MQTT_SPEC.md §5.1). The three
z=0 points (`hover_over_min/mid/max`) are required for basic motion; the
z=50 points (`hover_min_120/mid_120/max_120`) are optional and only needed
for non-zero-height reach.

The page is built around what this calibration actually is: not six numbers
to type, but six *shapes* to put the arm into. So each point shows the shape
it wants, gives the sliders to get there — which drive the real servos — and
records the angles the arm ended up at rather than the ones that were asked
for. Those differ, and the difference is the whole measurement: a servo told
165 may sit at 162, and 162 is the truth IK has to be built on.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ....services.network.pub_sub.calibration_messages import send_hover_point
from ....services.network.pub_sub.manual_messages import send_servo
from ...theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING
from .arm_pose import POSES, ArmPoseView
from .step import StepPage

GROUPS = (
    ("Table level (z = 0 mm)", (
        ("hover_over_min", "Min"),
        ("hover_over_mid", "Mid"),
        ("hover_over_max", "Max"),
    )),
    ("Raised (z = 50 mm) — optional", (
        ("hover_min_120", "Min"),
        ("hover_mid_120", "Mid"),
        ("hover_max_120", "Max"),
    )),
)

JOINTS = (("elbow", "ELBOW"), ("wrist", "WRIST"), ("twist", "TWIST"))


class IKPage(StepPage):
    key = "ik"
    step_key = "ik"
    label = "2 · IK"
    title = "Inverse Kinematics"
    subtitle = (
        "Put the arm into each shape, then capture the angles that made it."
    )

    def __init__(self, workspace=None) -> None:
        super().__init__(workspace)
        # The arm's live angles, from the robot's heartbeat, so a captured
        # pose records where the arm *is* rather than where it was told to
        # go. None until the first heartbeat arrives.
        self._live: dict | None = None
        self._live_unsubscribe = None
        self._points: list[HoverPoint] = []
        self._waiting: QLabel | None = None

    # ---- live angles -----------------------------------------------------
    def enter(self) -> None:
        """Start listening to the robot's heartbeat on the way in."""
        self._watch_heartbeat()

    def _watch_heartbeat(self) -> None:
        robot = self.workspace.robot
        if not robot or self._live_unsubscribe is not None:
            return
        self._live_unsubscribe = self.workspace.client().subscribe(
            f"{robot}/HEARTBEAT", self._on_heartbeat
        )

    def _on_heartbeat(self, _topic: str, payload: dict) -> None:
        """Called on the MQTT network thread — hand over, touch nothing.

        Same rule as StepPage._on_reply: the widgets these values feed live
        on the GUI thread, and updating them from here is what segfaulted a
        calibration run. StepPage's bridge is reused rather than duplicated.
        """
        self._bridge.arrived.emit({"__heartbeat__": payload})

    def _handle_reply(self, payload: dict) -> None:
        """Split the heartbeat stream back out from the command replies."""
        heartbeat = payload.get("__heartbeat__")
        if heartbeat is None:
            super()._handle_reply(payload)
            return

        first = self._live is None
        self._live = heartbeat
        # Only the drawings and the readout change, so they are updated in
        # place: a full rebuild several times a second would fight the user
        # for the sliders they are dragging.
        for point in self._points:
            point.set_live(heartbeat, adopt=first)
        if first and self._waiting is not None:
            self._waiting.setVisible(False)

    def live_angles(self) -> dict:
        """The arm's current angles, or {} before the first heartbeat."""
        if not self._live:
            return {}
        return {
            "elbow": self._live.get("ELBOW_ANGLE"),
            "wrist": self._live.get("WRIST_ANGLE"),
            "twist": self._live.get("TWIST_ANGLE"),
        }

    # ---- body ------------------------------------------------------------
    def build_form(self) -> QWidget:
        self._points = []
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(
            CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, CARD_MARGIN_V
        )
        layout.setSpacing(CARD_SPACING * 3)

        # Kept as a field so the first heartbeat can hide it in place. A
        # rebuild would be the obvious way and the wrong one: it happens
        # while the user may be mid-drag on a slider.
        self._waiting = QLabel(
            "Waiting for the robot's heartbeat — the live arm and the "
            "capture buttons appear once it reports in."
        )
        self._waiting.setObjectName("CardBody")
        self._waiting.setWordWrap(True)
        self._waiting.setVisible(bool(self.workspace.robot) and not self._live)
        layout.addWidget(self._waiting)

        for group_label, points in GROUPS:
            group = QGroupBox(group_label)
            inner = QVBoxLayout(group)
            inner.setSpacing(CARD_SPACING * 2)
            for calibration_type, name in points:
                point = HoverPoint(
                    calibration_type, name,
                    enabled=self.can_send,
                    on_jog=self._jog,
                    on_capture=self._capture,
                )
                point.set_live(self._live or {})
                self._points.append(point)
                inner.addWidget(point)
            layout.addWidget(group)

        return holder

    # ---- actions ---------------------------------------------------------
    def _jog(self, joint: str, position: int) -> None:
        """Move one servo on the real arm, live."""
        robot = self.workspace.robot
        if not robot:
            return
        send_servo(self.workspace.client(), robot, joint, position)

    def _capture(self, calibration_type: str, distance: float) -> None:
        """Record the pose the arm is in right now.

        The angles come from the heartbeat, not the sliders. A servo does not
        always land exactly where it was told, and the number IK needs is
        where the arm actually is — which is the entire reason this step
        exists rather than being a table of constants.
        """
        angles = self.live_angles()
        if not angles:
            return
        action_id = send_hover_point(
            self.workspace.client(), self.workspace.robot,
            calibration_type, distance,
            elbow=angles["elbow"], wrist=angles["wrist"], twist=angles["twist"],
        )
        self.send(action_id, waiting_text=f"Saving {calibration_type}…")


class HoverPoint(QWidget):
    """One hover point: the shape to make, the sliders, and Capture."""

    def __init__(self, calibration_type: str, name: str, *, enabled: bool,
                 on_jog, on_capture) -> None:
        super().__init__()
        self.calibration_type = calibration_type
        self.pose = POSES[calibration_type]

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(CARD_SPACING * 3)

        # The drawing carries the instruction, so it leads.
        self.view = ArmPoseView(self.pose)
        layout.addWidget(self.view)

        right = QVBoxLayout()
        right.setSpacing(CARD_SPACING)

        title = QLabel(f"{name} — {self.pose.summary}")
        title.setObjectName("CardTitle")
        title.setWordWrap(True)
        right.addWidget(title)

        hint = QLabel(self.pose.hint)
        hint.setObjectName("CardBody")
        hint.setWordWrap(True)
        right.addWidget(hint)

        # Sliders rather than spin boxes: this is a physical adjustment
        # watched on the robot itself, so a control that moves continuously
        # under the hand beats one that needs a number chosen in advance.
        self._sliders: dict[str, QSlider] = {}
        self._readouts: dict[str, QLabel] = {}
        for key, joint in JOINTS:
            right.addLayout(
                self._joint_row(key, joint, enabled, on_jog)
            )

        bottom = QHBoxLayout()
        bottom.setSpacing(CARD_SPACING)

        bottom.addWidget(QLabel("Distance"))
        self.distance = QDoubleSpinBox()
        self.distance.setRange(0, 500)
        self.distance.setSuffix(" mm")
        self.distance.setValue(self.pose.distance)
        self.distance.setToolTip(
            "The mark on the table the gripper is hovering over. The "
            "landmarks the stencil workflow expects are 0, 60 and 120 mm."
        )
        bottom.addWidget(self.distance)

        self.capture = QPushButton("Capture This Pose")
        self.capture.setObjectName("ToolbarAction")
        self.capture.setCursor(Qt.PointingHandCursor)
        self.capture.setEnabled(False)
        self.capture.setToolTip(
            "Save the angles the arm is at right now — not the slider "
            "values, which is what the robot was asked for rather than what "
            "it reached."
        )
        self.capture.clicked.connect(
            lambda: on_capture(self.calibration_type, self.distance.value())
        )
        bottom.addWidget(self.capture)
        bottom.addStretch(1)
        right.addLayout(bottom)

        right.addStretch(1)
        layout.addLayout(right, 1)
        self._enabled = enabled

    def _joint_row(self, key: str, joint: str, enabled: bool, on_jog):
        row = QHBoxLayout()
        row.setSpacing(CARD_SPACING)

        label = QLabel(joint.title())
        label.setObjectName("CardBody")
        label.setFixedWidth(52)
        row.addWidget(label)

        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 180)
        slider.setValue(int(getattr(self.pose, key)))
        slider.setEnabled(enabled)
        # sliderReleased, not valueChanged: dragging emits a value per pixel,
        # and a servo command per pixel would flood the broker and make the
        # arm chase the drag rather than follow it.
        slider.sliderReleased.connect(
            lambda k=joint, s=slider: on_jog(k, s.value())
        )
        row.addWidget(slider, 1)
        self._sliders[key] = slider

        readout = QLabel("—")
        readout.setObjectName("CardBody")
        readout.setFixedWidth(58)
        readout.setToolTip("Where this joint actually is, from the heartbeat.")
        row.addWidget(readout)
        self._readouts[key] = readout
        return row

    def set_live(self, heartbeat: dict, *, adopt: bool = False) -> None:
        """Show where the arm is, in the drawing and beside each slider.

        `adopt` moves the sliders to match, which is only right on the first
        heartbeat: after that the arm's position and the slider's are the
        same thing being driven from one end, and snapping the handle under
        a dragging hand would make the control fight back.
        """
        angles = {
            "elbow": heartbeat.get("ELBOW_ANGLE"),
            "wrist": heartbeat.get("WRIST_ANGLE"),
            "twist": heartbeat.get("TWIST_ANGLE"),
        }
        if any(value is None for value in angles.values()):
            return

        self.view.set_live(angles["elbow"], angles["wrist"], angles["twist"])
        for key, value in angles.items():
            self._readouts[key].setText(f"{value:g}°")
            if adopt:
                slider = self._sliders[key]
                slider.blockSignals(True)
                slider.setValue(int(value))
                slider.blockSignals(False)
        # Capture needs a live reading to record, so it stays disabled until
        # one arrives rather than saving whatever the sliders happen to say.
        self.capture.setEnabled(self._enabled)
