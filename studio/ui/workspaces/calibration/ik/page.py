"""Inverse Kinematics: joint lengths and limits, so a target position becomes
angles.

Sends one `calibrate` request per hover point (MQTT_SPEC.md §5.1). The three
z=0 points (`hover_over_min/mid/max`) are required for basic motion; the
z=50 points (`hover_min_120/mid_120/max_120`) are optional and only needed
for non-zero-height reach.

The page is built around what this calibration actually is: not six numbers
to type, but six *shapes* to put the arm into. So each point shows the shape
it wants, gives the elbow and wrist sliders to get there — which drive the
real servos — and records the angles the arm ended up at rather than the ones
that were asked for. Those differ, and the difference is the whole
measurement: a servo told 165 may sit at 162, and 162 is the truth IK has to
be built on.

Twist is not part of that: it turns the gripper without moving it, so it
cannot change the reach and height these points measure. It is posed once in
Base + Perch, and every hover snapshot here reports the same fixed
TWIST_ANGLE.

This module is the page: the heartbeat subscription, which pose is unlocked,
what a capture writes, and the servo→screen mapping read back out of those
captures. One pose's controls are `hover_point.HoverPoint`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtWidgets import (
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .....models.config.calibrations import Calibration, calibrations
from .....services.network.pub_sub.calibration_messages import send_hover_point
from .....services.network.pub_sub.manual_messages import send_servo
from .....services.network.pub_sub.robot_topics import heartbeat_topic
from ....components import Card
from ....theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING
from .arm_pose import POSES
from .hover_point import HoverPoint
from .points import CAPTURED_POINTS, GROUPS, JOINTS, TWIST_ANGLE
from .servo_frame import Frame, frame_from_captures
from ..step import StepPage


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
        # Which hover point is mid-capture, so the reply can be filed under
        # it. None whenever nothing is in flight.
        self._capturing: tuple[str, dict, float] | None = None
        self._in_capture = False
        # Which hover point is unlocked for editing, or "" for none. One at
        # a time: the six points drive one arm, so six live forms are six
        # ways to move it without meaning to.
        self._editing = ""

    # ---- live angles -----------------------------------------------------
    def enter(self) -> None:
        """Start listening to the robot's heartbeat on the way in."""
        self._watch_heartbeat()

    def _watch_heartbeat(self) -> None:
        robot = self.workspace.robot
        if not robot or self._live_unsubscribe is not None:
            return
        self._live_unsubscribe = self.workspace.client().subscribe(
            heartbeat_topic(robot), self._on_heartbeat
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
        frame = self._frame()
        # Only the point being edited adopts the arm's angles — it is the
        # one whose sliders can move, and starting them where the arm
        # already is saves a jump. Every other point keeps the shape it is
        # asking for, captured or not.
        adopting = next(
            (p for p in self._points
             if p.calibration_type == self._editing and p.captured is None),
            None,
        ) if first else None
        for point in self._points:
            point.set_live(
                heartbeat, adopt=point is adopting, frame=frame
            )
        if first and self._waiting is not None:
            self._waiting.setVisible(False)

    def live_angles(self) -> dict:
        """The arm's current angles, or {} before the first heartbeat.

        Elbow and wrist only. Twist is not read back because it is not sent
        back — see TWIST_ANGLE.
        """
        if not self._live:
            return {}
        return {
            "elbow": self._live.get("ELBOW_ANGLE"),
            "wrist": self._live.get("WRIST_ANGLE"),
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

        # Until two poses are captured, nothing can place this robot's servo
        # angles on the drawing, so the live arm is absent and the user is
        # told why rather than left wondering where the blue line went.
        frame = self._frame()
        if not frame.known:
            unmapped = QLabel(
                "The live arm appears once two poses are captured. Until "
                "then Studio cannot know how this robot's servo angles map "
                "onto the drawing — that mapping is what these captures "
                "measure. The angles beside each slider are live now."
            )
            unmapped.setObjectName("CardBody")
            unmapped.setWordWrap(True)
            layout.addWidget(unmapped)

        captured = self._captured_points()
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
                    on_pose=self._pose,
                    on_edit=self._edit,
                    captured=captured.get(calibration_type),
                    editing=calibration_type == self._editing,
                )
                point.set_live(self._live or {}, frame=frame)
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

    def _edit(self, calibration_type: str) -> None:
        """Unlock one hover point, locking whichever was unlocked before.

        Clicking the toggle on the point already being edited locks it, so
        the page can be put back to a state where nothing is live.
        """
        self._editing = "" if self._editing == calibration_type else calibration_type
        for point in self._points:
            point.set_editing(point.calibration_type == self._editing)

    def _pose(self, calibration_type: str) -> None:
        """Drive the arm back to the angles this point captured.

        Two `servo` commands rather than `controlik`: controlik asks the
        firmware to *solve* for a distance, which depends on the very
        calibration these captures are still building. The recorded angles
        are known good — they are what the arm was at when the shape
        matched — so they are sent directly.
        """
        robot = self.workspace.robot
        if not robot:
            return
        captured = self._captured_points().get(calibration_type)
        if not captured:
            return

        client = self.workspace.client()
        for key, joint in JOINTS:
            angle = captured.get(key)
            if isinstance(angle, (int, float)) and not isinstance(angle, bool):
                send_servo(client, robot, joint, int(angle))

        # The sliders are what will be sent next, so they follow the arm to
        # where it was just told to go — otherwise the handles still show
        # the previous pose while the metal is somewhere else.
        for point in self._points:
            if point.calibration_type != calibration_type:
                continue
            for key, _joint in JOINTS:
                angle = captured.get(key)
                if isinstance(angle, (int, float)) and not isinstance(angle, bool):
                    slider = point._sliders[key]
                    slider.blockSignals(True)
                    slider.setValue(int(angle))
                    slider.blockSignals(False)

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
        # Remembered so `on_completed` can file it under the point it came
        # from: the firmware's reply says what was written, not which of the
        # six shapes the user was matching when they pressed the button.
        self._capturing = (calibration_type, angles, distance)
        # Held across the whole exchange, including the rebuild that follows
        # the terminal reply — `_capturing` is cleared while saving, which is
        # before that last rebuild runs.
        self._in_capture = True
        action_id = send_hover_point(
            self.workspace.client(), self.workspace.robot,
            calibration_type, distance,
            elbow=angles["elbow"], wrist=angles["wrist"], twist=TWIST_ANGLE,
        )
        self.send(action_id, waiting_text=f"Saving {calibration_type}…")

    def rebuild(self) -> None:
        """Refresh in place while a capture is in flight.

        The inherited rebuild deletes and reconstructs all six hover points
        to swap in a "Waiting…" card. On this page that is destructive: each
        point carries slider positions the user posed by hand, and rebuilding
        drops them — the arm is left where it is while the controls that were
        driving it are replaced underneath. Capturing one point would also
        visibly reload the other five, which is what made a single capture
        look like it had changed every pose.

        Only the capture buttons and the captured labels actually change, so
        during a capture they are updated where they stand.
        """
        if not self._in_capture or not self._points:
            super().rebuild()
            return

        captured = self._captured_points()
        for point in self._points:
            point.set_captured(captured.get(point.calibration_type))
            point.set_enabled(self.can_send)
        # The exchange is over once nothing is pending; the next rebuild is
        # an ordinary one and may replace the form.
        if not self._pending:
            self._in_capture = False

    def on_completed(self, payload: dict) -> None:
        """Record this capture against the one hover point it came from.

        Six poses, six records — deliberately not the inherited merge. That
        flattens every reply into one dict of ELBOW/WRIST/DISTANCE, so each
        capture overwrites the last and the step ends up storing only the
        most recent pose while presenting it as the step's own values. It
        also reads, correctly, as though capturing one point had changed all
        six.

        What IK actually measures is six independent (shape, angles) pairs,
        so that is what is stored. Nothing here is global to the step.
        """
        captured = self._capturing
        if captured is None:
            # Not one of this page's captures — a reply to something else
            # entirely. Fall back rather than silently dropping it.
            super().on_completed(payload)
            return
        self._capturing = None

        calibration_type, angles, distance = captured
        robot = self.workspace.robot
        existing = calibrations().find(robot, self.step_key)
        values = dict(existing.values) if existing is not None else {}

        points = dict(values.get(CAPTURED_POINTS) or {})
        points[calibration_type] = {
            "elbow": angles["elbow"],
            "wrist": angles["wrist"],
            "twist": TWIST_ANGLE,
            "distance": distance,
        }
        values[CAPTURED_POINTS] = points

        calibrations().save(Calibration(
            robot=robot,
            step=self.step_key,
            values=values,
            saved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ))

    def build_saved_values(self) -> QWidget:
        """One line per captured pose, in the order the page presents them.

        The inherited card prints the values dict as it is stored, which for
        six nested poses is a wall of raw Python. What the user needs to know
        is which shapes are captured and which are still outstanding.
        """
        robot = self.workspace.robot
        saved = calibrations().find(robot, self.step_key) if robot else None
        if saved is None:
            return Card("Saved values", "Nothing stored.")

        points = saved.values.get(CAPTURED_POINTS) or {}
        if not points:
            return Card(f"Saved values — {saved.saved_at}", "No poses captured yet.")

        lines = []
        for _label, group in GROUPS:
            for calibration_type, name in group:
                angles = points.get(calibration_type)
                if angles is None:
                    lines.append(f"{name} ({calibration_type}): not captured")
                    continue
                # Distance arrived later than the first captures, so a
                # record written before it exists has none. Falling back to
                # the point's own target beats printing "at None mm".
                distance = angles.get("distance")
                if not isinstance(distance, (int, float)):
                    distance = POSES[calibration_type].distance
                lines.append(
                    f"{name} ({calibration_type}): "
                    f"elbow {angles.get('elbow')}°, wrist {angles.get('wrist')}°, "
                    f"at {distance:g} mm"
                )

        return Card(
            f"Saved values — {saved.saved_at}",
            f"{len(points)} of 6 poses captured\n\n" + "\n".join(lines),
        )

    # ---- the live arm's mapping -------------------------------------------
    def _captured_points(self) -> dict:
        """What each hover point has recorded, keyed by calibration type."""
        robot = self.workspace.robot
        saved = calibrations().find(robot, self.step_key) if robot else None
        if saved is None:
            return {}
        points = saved.values.get(CAPTURED_POINTS)
        return points if isinstance(points, dict) else {}

    def _frame(self) -> Frame:
        """How to draw this robot's live arm, from what it has captured."""
        return frame_from_captures(self._captured_points())

