"""Visual: the perch the camera looks from, and what it can see from there.

This is the step that turns a picture into a distance. Everything downstream
needs it: a detection comes back as a point in an 800x600 frame, `controlik`
takes millimetres of reach, and nothing connects the two until the arm has a
known camera pose and some marks on the table to measure against.

So the page does three things, in the order you actually do them:

1. Pose the arm. Elbow, wrist and twist sliders drive the real servos, with
   the live frame beside them — the camera is on the arm, so the only way to
   aim it is to move the arm and look. That pose is the *perch*: where the
   robot returns to before every photo, and therefore the one viewpoint every
   detection is taken from.

2. Save it, as perch_elbow/wrist/twist_angle.

3. Record what that view reaches: the near, middle and far marks on the table,
   in millimetres out from the turntable's edge. Photograph each one and the
   frame gives you where a known distance lands in the picture.

The perch angles used to live on step 1, beside the base rotation profile.
They were in the wrong place: a perch is a camera pose, not a property of the
base, and setting it blind on a page with no picture on it is guesswork. It is
here now, where the frame it produces is on screen next to the sliders that
produce it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....services.network.pub_sub.calibration_messages import (
    send_calibrate_depth,
    send_perch_value,
)
from ....services.network.pub_sub.manual_messages import send_base_home, send_servo
from ....services.network.pub_sub.robot_topics import heartbeat_topic, photo_topic
from ...theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING
from ..workflows.live_view import PhotoView
from .controls import PinnedSlider, ScrollSafeSpinBox, widest
from .step import StepPage

# The joints that aim the camera.
#
# Twist is not one of them. It rotates the gripper about its own axis, which
# changes how the hand is held but not where the camera points — a slider
# that cannot move the thing being set is one that can only be set wrong.
# The same reasoning keeps it off the IK page.
ANGLES = (
    ("elbow", "ELBOW", "Elbow"),
    ("wrist", "WRIST", "Wrist"),
)

# What twist is saved as. The same neutral, square-on wrist the IK hover
# points report, so the two calibrations describe the same hand.
TWIST_NEUTRAL = 90

# The three marks on the table this view is measured against, in millimetres
# out from the turntable's edge — the same origin the IK hover points use, so
# a distance measured here means the same thing to `controlik`.
REACHES = (
    ("min", "Near mark (mm)", 0.0),
    ("mid", "Middle mark (mm)", 60.0),
    ("max", "Far mark (mm)", 120.0),
)


class VisualPage(StepPage):
    key = "visual"
    step_key = "visual"
    label = "3 · Visual"
    title = "Visual"
    subtitle = (
        "Pose the camera, save the perch, then measure what it can reach."
    )

    def __init__(self, workspace=None) -> None:
        super().__init__(workspace)
        self._live: dict | None = None
        self._live_unsubscribe = None
        self._photo_unsubscribe = None

    # ---- watching the robot ----------------------------------------------
    def enter(self) -> None:
        self._watch()

    def _watch(self) -> None:
        robot = self.workspace.robot
        if not robot or self._live_unsubscribe is not None:
            return
        client = self.workspace.client()
        self._live_unsubscribe = client.subscribe(
            heartbeat_topic(robot), self._on_heartbeat
        )
        # Raw, because a photo frame is binary and decoding it as JSON drops
        # it. Both callbacks arrive on paho's thread and cross the step
        # page's own reply bridge before touching a widget.
        self._photo_unsubscribe = client.subscribe_raw(
            photo_topic(robot), self._on_photo
        )

    def _on_heartbeat(self, _topic: str, payload: dict) -> None:
        self._bridge.arrived.emit({"__heartbeat__": payload})

    def _on_photo(self, _topic: str, payload: bytes) -> None:
        self._bridge.arrived.emit({"__photo__": payload})

    def _handle_reply(self, payload: dict) -> None:
        """Split the two watched streams back out from the command replies."""
        heartbeat = payload.get("__heartbeat__")
        if heartbeat is not None:
            self._live = heartbeat
            if self._form is not None:
                self._form.set_live(heartbeat)
            return
        photo = payload.get("__photo__")
        if photo is not None:
            if self._form is not None:
                self._form.set_photo(photo)
            return
        super()._handle_reply(payload)

    # ---- the form ---------------------------------------------------------
    def build_form(self) -> QWidget:
        return VisualForm(
            enabled=self.can_send,
            on_jog=self._jog,
            on_save_angle=self._save_angle,
            on_save_reach=self._save_reach,
            on_capture=self._capture,
            on_home=self._home,
            live=self._live,
        )

    def _home(self) -> None:
        """Turn the base until it finds its true-north switch."""
        action_id = send_base_home(self.workspace.client(), self.workspace.robot)
        self.send(action_id, waiting_text="Finding true north…")

    def _jog(self, joint: str, position: int) -> None:
        """Move one servo, so the camera can be aimed by watching it."""
        robot = self.workspace.robot
        if not robot:
            return
        send_servo(self.workspace.client(), robot, joint, position)

    def _save_angle(self, field: str, value: float) -> None:
        action_id = send_perch_value(
            self.workspace.client(), self.workspace.robot, field, value
        )
        self.send(action_id, waiting_text=f"Saving perch {field}…")

    def _save_reach(self, field: str, value: float) -> None:
        action_id = send_perch_value(
            self.workspace.client(), self.workspace.robot, field, value
        )
        self.send(action_id, waiting_text=f"Saving {field} mark…")

    def _capture(self) -> None:
        action_id = send_calibrate_depth(self.workspace.client(), self.workspace.robot)
        self.send(action_id, waiting_text="Taking a photo from the perch…")


class VisualForm(QWidget):
    """Sliders on the left, the camera's own view on the right."""

    def __init__(self, *, enabled: bool, on_jog, on_save_angle, on_save_reach,
                 on_capture, on_home, live: dict | None = None) -> None:
        super().__init__()
        self._enabled = enabled
        # Joints the user has driven by hand. Until then the handles track
        # the arm; afterwards they are the user's to place.
        self._touched: set[str] = set()

        outer = QHBoxLayout(self)
        outer.setContentsMargins(
            CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, CARD_MARGIN_V
        )
        outer.setSpacing(CARD_SPACING * 3)

        left = QVBoxLayout()
        left.setSpacing(CARD_SPACING * 2)

        left.addWidget(self._heading(
            "1 · Aim the camera",
            "The camera rides on the arm, so pointing it means moving the "
            "arm. Watch the frame while you drag.",
        ))

        self._sliders: dict[str, PinnedSlider] = {}
        self._readouts: dict[str, QLabel] = {}
        for key, joint, label in ANGLES:
            left.addLayout(self._angle_row(key, joint, label, enabled, on_jog))

        save_row = QHBoxLayout()
        save_row.setSpacing(CARD_SPACING)

        # The base turns too, and a perch is only meaningful from a known
        # heading — the same pose facing two different ways sees two
        # different tables. Homing first is what makes the saved angles mean
        # one thing.
        self.home = QPushButton("Go to True North")
        self.home.setObjectName("SecondaryAction")
        self.home.setCursor(Qt.PointingHandCursor)
        self.home.setEnabled(enabled)
        self.home.setToolTip(
            "Turn the base until it finds its true-north switch. Do this "
            "before saving a perch, so the angles are measured from a "
            "heading the robot can return to."
        )
        self.home.clicked.connect(on_home)
        save_row.addWidget(self.home)

        self.save_perch = QPushButton("Save This Perch")
        self.save_perch.setObjectName("ToolbarPrimary")
        self.save_perch.setCursor(Qt.PointingHandCursor)
        self.save_perch.setEnabled(enabled)
        self.save_perch.setToolTip(
            "Store these three angles as the perch. Every photo the robot "
            "takes is taken from here, so every detection is measured "
            "against this one view."
        )
        self.save_perch.clicked.connect(self._save_perch_clicked)
        self._on_save_angle = on_save_angle
        save_row.addWidget(self.save_perch)
        save_row.addStretch(1)
        left.addLayout(save_row)

        left.addSpacing(CARD_SPACING * 2)
        left.addWidget(self._heading(
            "2 · Measure what it reaches",
            "Put a marker on the table at each distance, photograph it, and "
            "record the millimetres. This is what turns a point in the "
            "picture into a distance the arm can drive to.",
        ))

        self._reaches: dict[str, ScrollSafeSpinBox] = {}
        for key, label, default in REACHES:
            left.addLayout(self._reach_row(key, label, default, enabled, on_save_reach))

        capture_row = QHBoxLayout()
        capture_row.setSpacing(CARD_SPACING)
        self.capture = QPushButton("Take a Photo")
        self.capture.setObjectName("SecondaryAction")
        self.capture.setCursor(Qt.PointingHandCursor)
        self.capture.setEnabled(enabled)
        self.capture.clicked.connect(on_capture)
        capture_row.addWidget(self.capture)
        capture_row.addStretch(1)
        left.addLayout(capture_row)

        left.addStretch(1)
        outer.addLayout(left, 3)

        right = QVBoxLayout()
        right.setSpacing(CARD_SPACING)
        self.photo = PhotoView()
        self.photo.setMinimumHeight(260)
        right.addWidget(self.photo, 1)
        self.hint = QLabel("Take a photo to see what the camera sees.")
        self.hint.setObjectName("CardBody")
        self.hint.setWordWrap(True)
        right.addWidget(self.hint)
        outer.addLayout(right, 2)

        if live:
            self.set_live(live)

    def _save_perch_clicked(self) -> None:
        """Store the pose: the two aimed joints, and twist at its neutral.

        Twist has no slider — it cannot move the camera — but the firmware
        still reads a `perch_twist_angle`, and leaving it at whatever an
        earlier calibration wrote would mean the saved perch is not the pose
        on screen. TWIST_NEUTRAL is the square-on wrist the IK poses are
        drawn in and the one every hover snapshot reports.
        """
        for key, _joint, _label in ANGLES:
            self._on_save_angle(key, float(self._sliders[key].value()))
        self._on_save_angle("twist", float(TWIST_NEUTRAL))

    # ---- rows -------------------------------------------------------------
    @staticmethod
    def _heading(title: str, body: str) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        heading = QLabel(title)
        heading.setObjectName("CardTitle")
        layout.addWidget(heading)
        text = QLabel(body)
        text.setObjectName("CardBody")
        text.setWordWrap(True)
        layout.addWidget(text)
        return holder

    def _angle_row(self, key, joint, label, enabled, on_jog):
        row = QHBoxLayout()
        row.setSpacing(CARD_SPACING)

        caption = QLabel(label)
        caption.setObjectName("CardBody")
        caption.setMinimumWidth(
            widest(caption, *(name for _k, _j, name in ANGLES))
        )
        row.addWidget(caption)

        slider = PinnedSlider(0, 180, 90)
        slider.setEnabled(enabled)
        # Released, not per-pixel: a servo command per pixel of drag floods
        # the broker and makes the arm chase the hand.
        slider.committed.connect(lambda value, j=joint: on_jog(j, value))
        # Once the user has moved a joint, the handle is theirs. `drifted`
        # cannot answer this — a slider that has never been touched starts at
        # its default and is already "drifted" from wherever the arm happens
        # to be, which would stop it ever adopting the real angle.
        slider.committed.connect(lambda _value, k=key: self._touched.add(k))
        row.addWidget(slider, 1)
        self._sliders[key] = slider

        readout = QLabel("—")
        readout.setObjectName("CardBody")
        readout.setMinimumWidth(widest(readout, "now 180°"))
        readout.setToolTip("Where this joint is right now, from the heartbeat.")
        row.addWidget(readout)
        self._readouts[key] = readout
        return row

    def _reach_row(self, key, label, default, enabled, on_save):
        row = QHBoxLayout()
        row.setSpacing(CARD_SPACING)

        caption = QLabel(label)
        caption.setObjectName("CardBody")
        caption.setMinimumWidth(
            widest(caption, *(name for _k, name, _d in REACHES))
        )
        row.addWidget(caption)

        box = ScrollSafeSpinBox()
        box.setRange(0, 500)
        box.setValue(default)
        box.setEnabled(enabled)
        row.addWidget(box, 1)
        self._reaches[key] = box

        save = QPushButton("Set")
        save.setObjectName("ToolbarAction")
        save.setCursor(Qt.PointingHandCursor)
        save.setEnabled(enabled)
        save.clicked.connect(lambda _checked=False, k=key: on_save(k, self._reaches[k].value()))
        row.addWidget(save)
        return row

    # ---- what the page feeds it -------------------------------------------
    def set_live(self, heartbeat: dict) -> None:
        """Show where each joint is, and pin it on the slider."""
        for key, joint, _label in ANGLES:
            value = heartbeat.get(f"{joint}_ANGLE")
            if value is None:
                continue
            self._readouts[key].setText(f"now {value:g}°")
            slider = self._sliders[key]
            slider.set_pin(int(value))
            # The handle follows the arm until the user takes hold of it:
            # this page is for finding a pose, so starting where the arm
            # already is saves dragging back to it.
            if key not in self._touched and not slider.isSliderDown():
                slider.blockSignals(True)
                slider.setValue(int(value))
                slider.blockSignals(False)

    def set_photo(self, payload: bytes) -> None:
        from ....services.vision.frames import decode_frame

        try:
            frame = decode_frame(payload)
        except Exception:
            self.photo.set_photo(b"")
            self.hint.setText("The frame could not be read.")
            return
        self.photo.set_photo(frame.jpeg)
        self.hint.setText(
            "Line a marker up with each mark on the table, then record the "
            "distance beside it."
        )

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        for slider in self._sliders.values():
            slider.setEnabled(enabled)
        for box in self._reaches.values():
            box.setEnabled(enabled)
        self.save_perch.setEnabled(enabled)
        self.capture.setEnabled(enabled)
        self.home.setEnabled(enabled)
