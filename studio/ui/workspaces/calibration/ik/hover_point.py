"""One hover point: the shape to make, the sliders, and the buttons.

A card, repeated six times down the IK page. It owns the controls for one
pose and nothing about the page they sit on: which pose is being edited,
where the captures are stored, and what to do when one is taken are all the
page's business and arrive as callbacks.

That split is what keeps the edit lock honest. The card cannot decide it is
the one being edited — it is told — so "only one pose is live at a time"
stays a fact about the page rather than six widgets agreeing to behave.
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

from ....theme.metrics import CARD_SPACING
from .arm_pose import POSES, ArmPoseView
from .points import JOINTS, TWIST_ANGLE
from .servo_frame import Frame
from .slider import PinnedSlider, ScrollSafeSpinBox


def _widest(widget, *texts: str) -> int:
    """How wide `widget` must be to show the longest of `texts` in full.

    Asked of the widget rather than measured from its font, so whatever the
    stylesheet adds around the text — padding, a border — is counted by the
    thing that applies it. Every hardcoded width on this page was a guess at
    that number, correct at one zoom level and clipping above it.
    """
    remember = widget.text()
    try:
        widest = 0
        for text in texts:
            widget.setText(text)
            widest = max(widest, widget.sizeHint().width())
        return widest
    finally:
        widget.setText(remember)


class HoverPoint(QWidget):
    """One hover point: the shape to make, the sliders, and Capture."""

    def __init__(self, calibration_type: str, name: str, *, enabled: bool,
                 on_jog, on_capture, on_pose, on_edit,
                 captured: dict | None = None, editing: bool = False) -> None:
        super().__init__()
        self.setObjectName("HoverPoint")
        self.calibration_type = calibration_type
        self.pose = POSES[calibration_type]
        self.captured = captured
        # Both set before any widget is built: set_captured() reads them to
        # decide what can be pressed.
        self._enabled = enabled
        self._editing = editing

        layout = QHBoxLayout(self)
        # Room for the border drawn around the card, so the drawing and the
        # sliders do not sit hard against it.
        layout.setContentsMargins(
            CARD_SPACING * 2, CARD_SPACING * 2, CARD_SPACING * 2, CARD_SPACING * 2
        )
        layout.setSpacing(CARD_SPACING * 3)

        # The drawing carries the instruction, so it leads.
        self.view = ArmPoseView(self.pose)
        layout.addWidget(self.view)

        right = QVBoxLayout()
        right.setSpacing(CARD_SPACING)

        # The title carries the edit toggle. Only one point on the page can
        # be unlocked at a time: every control here drives the same physical
        # arm, so six live forms are six ways to move it by accident — and a
        # slider nudged on a point the user is not looking at has already
        # sent a servo command by the time they notice.
        title_row = QHBoxLayout()
        title_row.setSpacing(CARD_SPACING)

        # A word, not a pencil glyph: the app's font stack is text faces, so
        # ✎ fell through to a blank box on a button whose whole job is to
        # say what it does.
        self.edit_toggle = QPushButton("Edit")
        self.edit_toggle.setObjectName("SecondaryAction")
        self.edit_toggle.setCheckable(True)
        self.edit_toggle.setCursor(Qt.PointingHandCursor)
        # Never a fixed pixel width. The label is text, and text is as wide
        # as the theme's font at the user's zoom makes it — a hardcoded 60px
        # fitted "Edit" at zoom 1.0 and ate the middle of it at 1.3, which
        # is how a button ends up reading "di".
        #
        # The width is asked of the button itself, for the longest label it
        # can show, so the stylesheet's padding and border are counted by the
        # thing that applies them rather than guessed at here. Reserving the
        # longest also keeps the title row still when the label swaps.
        self.edit_toggle.setMinimumWidth(_widest(self.edit_toggle, "Edit", "Done"))
        self.edit_toggle.setToolTip(
            "Edit this pose. Unlocking it locks whichever pose was being "
            "edited — the sliders drive one real arm, so only one is live "
            "at a time."
        )
        self.edit_toggle.clicked.connect(
            lambda: on_edit(self.calibration_type)
        )
        title_row.addWidget(self.edit_toggle)

        title = QLabel(f"{name} — {self.pose.summary}")
        title.setObjectName("CardTitle")
        title.setWordWrap(True)
        title_row.addWidget(title, 1)
        right.addLayout(title_row)

        hint = QLabel(self.pose.hint)
        hint.setObjectName("CardBody")
        hint.setWordWrap(True)
        right.addWidget(hint)

        # What *this* point has recorded, which is the thing the live
        # readouts below cannot say: they are the one arm, shared by all six.
        self.captured_label = QLabel()
        self.captured_label.setObjectName("CardBody")
        self.captured_label.setWordWrap(True)
        right.addWidget(self.captured_label)

        # Sliders rather than spin boxes: this is a physical adjustment
        # watched on the robot itself, so a control that moves continuously
        # under the hand beats one that needs a number chosen in advance.
        self._sliders: dict[str, PinnedSlider] = {}
        self._readouts: dict[str, QLabel] = {}
        self._snap_backs: dict[str, QPushButton] = {}
        for key, joint in JOINTS:
            right.addLayout(
                self._joint_row(key, joint, enabled, on_jog)
            )

        bottom = QHBoxLayout()
        bottom.setSpacing(CARD_SPACING)

        bottom.addWidget(QLabel("Distance"))
        self.distance = ScrollSafeSpinBox()
        self.distance.setRange(0, 500)
        self.distance.setSuffix(" mm")
        self.distance.setValue(self.pose.distance)
        self.distance.setToolTip(
            "The mark on the table the gripper is hovering over. The "
            "landmarks the stencil workflow expects are 0, 60 and 120 mm."
        )
        bottom.addWidget(self.distance)

        self.capture = QPushButton("Capture This Pose")
        self.capture.setObjectName("SecondaryAction")
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

        # Drives the arm back to what this point recorded. Only meaningful
        # once there is something recorded, so it is disabled until then
        # rather than hidden — an empty gap beside Capture would not say
        # that returning to a pose is something the page can do.
        self.pose_button = QPushButton("Go to Saved Pose")
        self.pose_button.setObjectName("SecondaryAction")
        self.pose_button.setCursor(Qt.PointingHandCursor)
        self.pose_button.setToolTip(
            "Send the arm back to the angles this point captured. Useful "
            "for checking a saved pose still matches the shape, and for "
            "recapturing one without posing it by hand again."
        )
        self.pose_button.clicked.connect(
            lambda: on_pose(self.calibration_type)
        )
        bottom.addWidget(self.pose_button)
        bottom.addStretch(1)
        right.addLayout(bottom)

        right.addStretch(1)
        layout.addLayout(right, 1)

        # Last: these fill the captured label, set the toggle's own text, and
        # decide what is pressable, so every widget must already exist.
        self.set_captured(captured)
        self.set_editing(editing)

    def _joint_row(self, key: str, joint: str, enabled: bool, on_jog):
        row = QHBoxLayout()
        row.setSpacing(CARD_SPACING)

        # Minimum, not fixed: the columns still line up row to row, but the
        # label grows with the font instead of clipping at a zoom nobody
        # measured it at. Sized from the widest joint name there is.
        label = QLabel(joint.title())
        label.setObjectName("CardBody")
        label.setMinimumWidth(
            _widest(label, *(name.title() for _key, name in JOINTS))
        )
        row.addWidget(label)

        # A captured point opens on the angles it recorded, so returning to
        # it shows what was measured rather than the drawing's target — the
        # two are in different spaces, and the servo one is what this
        # control sends.
        recorded = self.captured.get(key) if self.captured else None
        slider = PinnedSlider(
            0, 180,
            int(recorded) if isinstance(recorded, (int, float))
            else int(getattr(self.pose, key)),
        )
        slider.setEnabled(enabled)
        # committed, not valueChanged: dragging emits a value per pixel, and
        # a servo command per pixel would flood the broker and make the arm
        # chase the drag rather than follow it.
        slider.committed.connect(lambda value, k=joint: on_jog(k, value))
        row.addWidget(slider, 1)
        self._sliders[key] = slider

        # Shown only once the handle is off the pin, but its space is always
        # reserved: `setVisible` on a laid-out widget re-flows the row, which
        # made the distance field and the capture button jump sideways every
        # time a slider crossed its pin. A retained size policy keeps the gap
        # whether or not the button is in it.
        # A word, for the same reason the edit toggle is one: ↩ is a symbol
        # the app's text faces may not carry, and it was also clipped by a
        # fixed 28px at every zoom the app offers.
        back = QPushButton("Reset")
        back.setObjectName("SecondaryAction")
        back.setCursor(Qt.PointingHandCursor)
        policy = back.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        back.setSizePolicy(policy)
        back.setVisible(False)
        back.setToolTip(
            "Put this joint back to the angle this pose captured. The pin "
            "on the groove marks the saved value; the handle is where the "
            "joint has been asked to go."
        )
        back.clicked.connect(slider.snap_back)
        slider.valueChanged.connect(
            lambda _value, s=slider, b=back: b.setVisible(s.drifted)
        )
        row.addWidget(back)
        self._snap_backs[key] = back

        # "now 137°" rather than "137°". There is one arm and six rows, so
        # the same live reading appears six times — unlabelled, that reads
        # as one capture having overwritten every pose, when in fact the
        # slider beside it still holds this point's own target.
        readout = QLabel("—")
        readout.setObjectName("CardBody")
        # Wide enough for the longest reading this can ever show, measured
        # rather than guessed — "now 180°" at a large zoom was clipping to
        # "now 18" and quietly misreporting the arm.
        readout.setMinimumWidth(_widest(readout, "now 180°"))
        readout.setToolTip(
            "Where this joint is right now, from the heartbeat. The arm is "
            "one arm, so every point shows the same live angle; the slider "
            "beside it is this point's own target."
        )
        row.addWidget(readout)
        self._readouts[key] = readout
        return row

    def set_captured(self, captured: dict | None) -> None:
        """Update what this point has recorded, without rebuilding it.

        The pin belongs to this point and nothing else. It marks the angle
        *this* shape saved, so six points show six different pins — pinning
        the live arm instead gave all six the same value and made capturing
        one point appear to move every other point's mark.
        """
        self.captured = captured
        self.captured_label.setText(
            f"Captured: elbow {captured.get('elbow')}°, "
            f"wrist {captured.get('wrist')}°"
            if captured else "Not captured yet."
        )
        for key, slider in self._sliders.items():
            recorded = captured.get(key) if captured else None
            slider.set_pin(
                int(recorded)
                if isinstance(recorded, (int, float))
                and not isinstance(recorded, bool)
                else None
            )
            if key in self._snap_backs:
                self._snap_backs[key].setVisible(slider.drifted)
        # "Go to Saved Pose" depends on there being something saved, so the
        # lock is re-applied rather than the button poked directly.
        self._apply_lock()

    def set_enabled(self, enabled: bool) -> None:
        """Whether this point *could* act, ignoring the edit lock."""
        self._enabled = enabled
        self._apply_lock()

    def set_editing(self, editing: bool) -> None:
        """Unlock this point's controls, or lock them again."""
        self._editing = editing
        self.edit_toggle.setChecked(editing)
        # The label names what the button does next, not the state it is in:
        # a checked button reading "Edit" invites a click that would close
        # the pose the user just opened.
        self.edit_toggle.setText("Done" if editing else "Edit")
        self._apply_lock()

    def _apply_lock(self) -> None:
        """One place decides what is pressable.

        Every control that can move the arm or change a saved value needs
        both: a robot to talk to, and this point being the one the user
        chose to edit. Splitting that across the call sites is how a control
        ends up live on a locked card.
        """
        live = self._enabled and self._editing

        # Qt does not restyle on a property change, so the border is
        # repolished by hand. Set from `_editing` alone rather than `live`:
        # with no robot every card would otherwise look identical, and which
        # pose is open is still worth showing.
        if self.property("editing") != self._editing:
            self.setProperty("editing", self._editing)
            self.style().unpolish(self)
            self.style().polish(self)
        for slider in self._sliders.values():
            slider.setEnabled(live)
        for back in self._snap_backs.values():
            back.setEnabled(live)
        self.distance.setEnabled(live)
        self.capture.setEnabled(live)
        self.pose_button.setEnabled(bool(self.captured) and live)
        # The toggle itself stays available whenever there is a robot, or
        # there would be no way to unlock a card once everything is locked.
        self.edit_toggle.setEnabled(self._enabled)

    def set_live(self, heartbeat: dict, *, adopt: bool = False,
                 frame: Frame | None = None) -> None:
        """Show where the arm is, in the drawing and beside each slider.

        `adopt` moves this point's sliders to match the arm. It applies to
        one point at a time — the one being posed — because there is one arm
        and six points: adopting on all of them replaces six distinct
        starting shapes with six copies of wherever the arm happens to be,
        which is what made every row read 137/59 after a single capture.

        `frame` maps this robot's servo degrees into the drawing's. Until two
        poses have been captured there is no such mapping, and the live arm
        is left off the drawing rather than drawn somewhere invented — the
        readouts beside each slider still report the real angles, which is
        the honest way to show a number nothing can place yet.
        """
        angles = {
            "elbow": heartbeat.get("ELBOW_ANGLE"),
            "wrist": heartbeat.get("WRIST_ANGLE"),
        }
        if any(value is None for value in angles.values()):
            return

        # The drawing is a side-on view, so it still needs a twist to render
        # — it gets the fixed one this page reports, which is also the one
        # the poses were drawn in.
        on_screen = (
            frame.to_screen(angles["elbow"], angles["wrist"])
            if frame is not None else None
        )
        if on_screen is None:
            self.view.clear_live()
        else:
            self.view.set_live(on_screen[0], on_screen[1], TWIST_ANGLE)
        for key, value in angles.items():
            self._readouts[key].setText(f"now {value:g}°")
            slider = self._sliders[key]
            if adopt:
                slider.blockSignals(True)
                slider.setValue(int(value))
                slider.blockSignals(False)
            self._snap_backs[key].setVisible(slider.drifted)
        # Capture needs a live reading to record, so it stays disabled until
        # one arrives rather than saving whatever the sliders happen to say
        # — and, now, until this point is the one being edited.
        self._apply_lock()
