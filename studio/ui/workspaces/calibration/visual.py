"""Visual: the perch the camera looks from, and what it can see from there.

This is the step that turns a picture into a distance. Everything downstream
needs it: a detection comes back as a point in an 800x600 frame, `controlik`
takes millimetres of reach, and nothing connects the two until the arm has a
known camera pose and some marks on the table to measure against.

So the page does three things, numbered in the order you actually do them:

1. Get all nine dots in view. Elbow and wrist sliders drive the real servos
   with the live frame beside them — the camera is on the arm, so the only
   way to aim it is to move the arm and look.

2. Calibrate the grid, and look at what came back. Vision finds the three
   angle lanes and three depth bands and draws them over the frame; if they
   are wrong the fix is to re-aim and run it again, which is why this step
   sits next to the sliders rather than after a commit.

3. Save the perch, as perch_elbow/wrist/twist_angle. That pose is where the
   robot returns before every photo, and therefore the one viewpoint every
   detection is measured from.

Steps 2 and 3 are mutually dependent, and the numbering reflects which way
round the user meets them rather than which the firmware wants: calibrating
is refused until a perch exists (`perch_not_configured`), but a perch saved
before you can see the dots is a guess. Aim, try, save, try again.

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

from ....models.config.calibrations import calibrations
from ....services.network.pub_sub.calibration_messages import (
    send_calibrate_depth,
    send_perch_value,
)
from ....services.network.pub_sub.manual_messages import send_base_home, send_servo
from ....services.network.pub_sub.robot_topics import heartbeat_topic, photo_topic
from ....services.network.pub_sub.robot_topics import vision_topic
from ...components import Card
from ...theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING
from ..workflows.live_view import PhotoView
from .controls import PinnedSlider, widest
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

def _first(values: dict, *keys: str):
    """The first key that holds a number, for a record that may use either
    spelling of the perch fields."""
    for key in keys:
        value = values.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


class VisualPage(StepPage):
    key = "visual"
    step_key = "visual"
    label = "3 · Visual"
    title = "Visual"
    subtitle = (
        "Aim the camera at the nine-dot sheet, calibrate the grid, and save "
        "the perch you calibrated from."
    )

    def __init__(self, workspace=None) -> None:
        super().__init__(workspace)
        self._live: dict | None = None
        self._live_unsubscribe = None
        self._photo_unsubscribe = None
        self._vision_unsubscribe = None
        self._perch_queue: list[tuple[str, float]] = []
        self._saving_perch = False
        self._awaiting_visual = False
        self._last_calibration: dict | None = None

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
        self._vision_unsubscribe = client.subscribe(
            vision_topic(robot), self._on_vision
        )

    def _on_heartbeat(self, _topic: str, payload: dict) -> None:
        self._bridge.arrived.emit({"__heartbeat__": payload})

    def _on_photo(self, _topic: str, payload: bytes) -> None:
        self._bridge.arrived.emit({"__photo__": payload})

    def _on_vision(self, _topic: str, payload: dict) -> None:
        self._bridge.arrived.emit(payload)

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
        if (
            payload.get("sender") == "visual_ai"
            and payload.get("type") == "calibrate_depth"
            and payload.get("action_id") == self._pending
        ):
            self._last_calibration = dict(payload)
            if self._form is not None:
                self._form.set_calibration(payload)
        if payload.get("action_id") == self._pending and payload.get("status") == "failed":
            # A pose failure comes from firmware; an extractor/profile failure
            # comes from Vision. Either terminal failure ends the workflow and
            # must not leave the sequential perch writer running.
            self._awaiting_visual = False
            self._saving_perch = False
            self._perch_queue.clear()
        super()._handle_reply(payload)

    # ---- the form ---------------------------------------------------------
    def build_form(self) -> QWidget:
        return VisualForm(
            enabled=self.can_send,
            on_jog=self._jog,
            on_save_perch=self._save_perch,
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

    def _save_perch(self, values: dict[str, float]) -> None:
        # Firmware has one calibration write per field. Send the next write
        # only after the exact terminal acknowledgement for the previous one;
        # the MQTT receive slot is deliberately synchronous.
        self._perch_queue = [
            (name, float(values[name]))
            for name in ("elbow", "wrist", "twist")
        ]
        self._saving_perch = True
        self._send_next_perch()

    def _send_next_perch(self) -> None:
        if not self._perch_queue:
            self._saving_perch = False
            return
        field, value = self._perch_queue.pop(0)
        action_id = send_perch_value(
            self.workspace.client(), self.workspace.robot, field, value
        )
        self.send(action_id, waiting_text=f"Saving perch {field}…")

    def _capture(self) -> None:
        self._awaiting_visual = True
        self._last_calibration = None
        action_id = send_calibrate_depth(self.workspace.client(), self.workspace.robot)
        self.send(
            action_id,
            waiting_text=(
                "Homing, moving to the perch, and calibrating nine dots…"
            ),
        )

    def is_terminal(self, payload: dict) -> bool:
        if self._awaiting_visual:
            return (
                payload.get("sender") == "visual_ai"
                and payload.get("type") == "calibrate_depth"
                and payload.get("status") == "completed"
            )
        return super().is_terminal(payload)

    def on_completed(self, payload: dict) -> None:
        super().on_completed(payload)
        if self._saving_perch:
            self._send_next_perch()
        if payload.get("sender") == "visual_ai" and payload.get("type") == "calibrate_depth":
            self._awaiting_visual = False

    def build_saved_values(self) -> QWidget:
        """What this one calibration is, in the terms the page works in.

        The inherited card prints the stored values dict as-is. On this page
        that dict is mostly the firmware's own `calibrationvalues` reply —
        encoder counts, gear teeth, speed profiles — flattened in by the
        default `results()` when each perch field is written. Forty lines of
        base-rotation telemetry under a heading that reads like success, on
        a page whose actual question is "am I calibrated or not", answers
        the wrong question loudly.

        So this shows the two things that decide it: whether a perch is
        saved, and whether a nine-point profile came out of it.
        """
        robot = self.workspace.robot
        saved = calibrations().find(robot, self.step_key) if robot else None
        if saved is None:
            return Card(
                "Not calibrated",
                "No perch saved and no nine-point profile for this robot. "
                "Work through steps 1–3 above.",
            )

        values = saved.values
        lines = []

        # The firmware reports a written perch field under its *reportKey*
        # — PERCH_ELBOW_ANGLE — not under the lowercase `calibration_type`
        # the request was sent with. `results()` stores whatever `TYPE` the
        # reply carried, so the record is uppercase. Both are accepted here
        # because a record written by an older build may hold either, and a
        # card that reads one spelling reports a saved perch as missing.
        perch = {
            name: _first(
                values, f"PERCH_{name.upper()}_ANGLE", f"perch_{name}_angle"
            )
            for name in ("elbow", "wrist", "twist")
        }
        perch_saved = all(value is not None for value in perch.values())
        if perch_saved:
            lines.append(
                f"Perch: elbow {perch['elbow']:g}°, wrist {perch['wrist']:g}°, "
                f"twist {perch['twist']:g}°"
            )
        else:
            lines.append("Perch: not saved — step 3 has not run for this robot.")

        profile_id = values.get("profile_id")
        if profile_id:
            cells = values.get("grid_cells")
            width, height = values.get("image_width"), values.get("image_height")
            lines.append(
                f"Grid: {values.get('point_count', 0)} of 9 dots anchored, "
                f"{cells} cells"
            )
            if width and height:
                lines.append(f"Captured from a {width}×{height} frame")
            lines.append(f"Profile: {profile_id}")
            # Both halves, or neither. A grid without a perch is a grid
            # measured from a viewpoint the robot cannot return to, and a
            # title reading "Calibrated" over a body reading "not saved" is
            # the contradiction this card exists to stop.
            title = (
                f"Calibrated — {saved.saved_at}" if perch_saved
                else f"Partly calibrated — {saved.saved_at}"
            )
        else:
            lines.append(
                "Grid: no nine-point profile yet. Until step 2 succeeds, "
                "Vision → Detections cannot turn a detection into a distance "
                "and Reach & Grab stays locked."
            )
            title = f"Not calibrated — {saved.saved_at}"

        return Card(title, "\n".join(lines))

    def results(self, payload: dict) -> dict:
        if payload.get("sender") == "visual_ai" and payload.get("type") == "calibrate_depth":
            profile = payload.get("calibration_profile") or {}
            return {
                "profile_id": payload.get("profile_id", ""),
                "point_count": len(payload.get("calibration_points") or {}),
                "grid_cells": len((payload.get("calibration_grid") or {}).get("zones", ())),
                "image_width": profile.get("image_width"),
                "image_height": profile.get("image_height"),
                "capture_pose": profile.get("capture_pose"),
                "algorithm": profile.get("algorithm"),
            }
        return super().results(payload)


class VisualForm(QWidget):
    """Sliders on the left, the camera's own view on the right."""

    def __init__(
        self, *, enabled: bool, on_jog, on_save_perch, on_capture, on_home,
        live: dict | None = None,
    ) -> None:
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
            "1 · Get all nine dots in view",
            "Place the nine-dot sheet in front of the robot: three lanes at "
            "-30°, 0°, and +30°, with dots at 0, 60, and 120 mm. The camera "
            "rides on the arm, so aiming it means moving the arm — drag "
            "until all nine dots sit unobstructed in the frame.",
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
        save_row.addStretch(1)
        left.addLayout(save_row)

        left.addSpacing(CARD_SPACING * 2)
        left.addWidget(self._heading(
            "2 · Calibrate the grid until the dots look right",
            "Calibrating photographs the sheet and asks Vision to find the "
            "three lanes and three depth bands. The frame beside this shows "
            "what it found: nine rings, three clean rows, three clean "
            "columns. If they are wrong, re-aim in step 1 and run it again.",
        ))

        capture_row = QHBoxLayout()
        capture_row.setSpacing(CARD_SPACING)
        self.capture = QPushButton("Calibrate 9-Point Grid")
        self.capture.setObjectName("ToolbarPrimary")
        self.capture.setCursor(Qt.PointingHandCursor)
        self.capture.setEnabled(enabled)
        self.capture.clicked.connect(on_capture)
        capture_row.addWidget(self.capture)
        capture_row.addStretch(1)
        left.addLayout(capture_row)

        left.addSpacing(CARD_SPACING * 2)
        left.addWidget(self._heading(
            "3 · Save this perch",
            "Saving stores these angles as the perch: the one pose every "
            "photo is taken from, and therefore the viewpoint every "
            "detection is measured against. The firmware will not calibrate "
            "a grid until a perch is saved, so if step 2 reported "
            "perch_not_configured, save here and run it again.",
        ))

        perch_row = QHBoxLayout()
        perch_row.setSpacing(CARD_SPACING)
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
        self._on_save_perch = on_save_perch
        perch_row.addWidget(self.save_perch)
        perch_row.addStretch(1)
        left.addLayout(perch_row)

        left.addStretch(1)
        outer.addLayout(left, 3)

        right = QVBoxLayout()
        right.setSpacing(CARD_SPACING)
        self.photo = PhotoView()
        self.photo.setMinimumHeight(260)
        right.addWidget(self.photo, 1)
        self.hint = QLabel(
            "Aim until all nine dots are in frame, then calibrate. What "
            "Vision found — the nine dots and every grid cell — is drawn "
            "here, so this is where you check the result before saving."
        )
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
        values = {
            key: float(self._sliders[key].value())
            for key, _joint, _label in ANGLES
        }
        values["twist"] = float(TWIST_NEUTRAL)
        self._on_save_perch(values)

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
            "Photo received. Waiting for Vision to identify and validate all "
            "nine dots…"
        )

    def set_calibration(self, result: dict) -> None:
        self.photo.set_calibration(result)
        if result.get("status") == "completed":
            profile_id = str(result.get("profile_id") or "")
            cells = len((result.get("calibration_grid") or {}).get("zones", ()))
            self.hint.setText(
                f"Calibration saved: 9 anchors, {cells} cells, profile "
                f"{profile_id[:12]}.")
            return
        error = result.get("error") or {}
        message = str(error.get("message") or "The nine-point grid was rejected.")
        # A missing perch is not a bad photograph. Vision extracts the dots
        # *before* it asks firmware for the perch, so this failure arrives
        # with nine good anchors already drawn on the frame beside it —
        # saying only "rejected" there sends the user back to re-aim a shot
        # that was fine.
        if str(error.get("code") or "") == "perch_not_configured":
            message += " The dots above were read correctly; only the perch is missing."
        self.hint.setText(message)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        for slider in self._sliders.values():
            slider.setEnabled(enabled)
        self.save_perch.setEnabled(enabled)
        self.capture.setEnabled(enabled)
        self.home.setEnabled(enabled)
