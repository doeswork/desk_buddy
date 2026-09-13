"""The IK step: the drawing's geometry, the edit lock, and what a capture saves.

    QT_QPA_PLATFORM=offscreen python -m studio.ui.workspaces.calibration.ik.tests

Several of these guard bugs that shipped, so they are written as the symptom
rather than the mechanism: the live arm drawn below the table, six sliders
showing one angle, a pin that moved when another pose was captured, and the
scroll wheel driving a servo on the way past.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from .....models.config.calibrations import Calibrations
from .....storage.store import Store
from .. import step as step_module

_app = QApplication.instance() or QApplication([])


def store() -> Calibrations:
    return Calibrations(Store("calibrations", directory=Path(tempfile.mkdtemp())))


# ---- The IK page: shapes, jogging, and capture ---------------------------


def ik_page():
    """An IK page with a robot selected and a stub client."""
    from . import IKPage

    sent: list = []

    class FakeClient:
        def publish(self, topic, payload, **_kwargs):
            sent.append((topic, payload))
            return "action-id"

        def subscribe(self, _topic, _callback):
            return lambda: None

    workspace = type("Workspace", (), {
        "robot": "black",
        "client": lambda self=None: FakeClient(),
        "robots": lambda self=None: [],
    })()
    view = IKPage(workspace)
    view.widget()
    return view, sent


def test_every_hover_point_has_a_shape_to_aim_at() -> None:
    """The calibration asks for a pose, so every point must describe one —
    a missing entry would render a blank drawing beside a live arm."""
    from .arm_pose import POSES
    from . import GROUPS

    for _label, points in GROUPS:
        for calibration_type, _name in points:
            pose = POSES[calibration_type]
            assert pose.summary and pose.hint, calibration_type


def tip_of(pose) -> tuple[float, float]:
    """Where a pose puts the gripper, in millimetres: (reach, height).

    Mirrors `_draw_arm` exactly, including the screen convention — cos adds
    height rather than subtracting it. Getting that backwards is what drew
    the z=0 row above the table and the z=50 row below it, so the test does
    the arithmetic the same way the paint code does or it proves nothing.
    """
    import math

    from .arm_pose import (
        BASE_RADIUS, FOREARM, GRIPPER, SHOULDER_HEIGHT, UPPER_ARM, ArmPoseView,
    )

    # The arm hangs off the shoulder pivot, not the top of the turntable:
    # the post between them is part of the machine, and starting the maths
    # at the wrong one of the two puts every tip an inch out.
    upper = math.radians(pose.elbow)
    x = math.sin(upper) * UPPER_ARM
    z = SHOULDER_HEIGHT + math.cos(upper) * UPPER_ARM
    fore = upper + math.radians(180 - pose.wrist)
    x += math.sin(fore) * (FOREARM + GRIPPER)
    z += math.cos(fore) * (FOREARM + GRIPPER)
    per_mm = ArmPoseView._mm_to_units()
    # Reach is quoted from the turntable's edge, not its centre — zero reach
    # is the gripper parked beside the base, not buried in the middle of it.
    return (x - BASE_RADIUS) / per_mm, z / per_mm


def test_the_drawing_has_the_real_robots_proportions() -> None:
    """Measured off the machine, not invented to look plausible.

    The drawing was a stick figure of some other robot: a forearm shorter
    than its upper arm and no base at all, on a machine whose forearm is
    half again as long and which stands on a wide turntable. Anyone matching
    a pose against it was matching the wrong shape.
    """
    from .arm_pose import (
        BASE_HEIGHT, HAND, SHOULDER_HEIGHT, UNITS_PER_INCH, UPPER_ARM,
    )

    inches = lambda units: units / UNITS_PER_INCH  # noqa: E731
    assert inches(BASE_HEIGHT) == 1.5, "the turntable is 1.5 in tall"
    assert inches(SHOULDER_HEIGHT) == 2.5, "the elbow pivot is 2.5 in up"
    assert inches(UPPER_ARM) == 3.5, "elbow pivot to wrist pivot"
    assert inches(HAND) == 4.5, "wrist pivot to fingertips"
    # The one that was backwards, and the reason the silhouette read wrong.
    assert HAND > UPPER_ARM


def test_a_millimetre_is_a_millimetre() -> None:
    """The old drawing needed a fitted fudge factor here because its
    proportions were invented — the arm could only touch the 120 mm mark by
    stretching dead straight, so the scale was bent until that looked
    right. Real lengths make the conversion the real one."""
    from .arm_pose import UNITS_PER_INCH, ArmPoseView

    assert ArmPoseView._mm_to_units() == UNITS_PER_INCH / 25.4


def test_the_arm_reaches_well_past_the_last_calibration_mark() -> None:
    """120 mm is a landmark inside the workspace, not the edge of it.

    Worth pinning: if the segment lengths are ever "tidied", a drawing whose
    max reach falls near 120 mm is one where every far pose is stretched
    straight, which is exactly the wrong shape the old geometry produced.
    """
    import math

    from .arm_pose import HAND, SHOULDER_HEIGHT, UPPER_ARM, ArmPoseView

    radius = UPPER_ARM + HAND
    reach = math.sqrt(radius ** 2 - SHOULDER_HEIGHT ** 2)
    millimetres = reach / ArmPoseView._mm_to_units()
    assert 170 < millimetres < 210, millimetres


def test_zero_reach_parks_the_gripper_beside_the_base_not_inside_it() -> None:
    """Distances are measured from the turntable's edge.

    Drawn from the centre instead, the Min pose put the gripper in the
    middle of the base — through the machine it is standing on — which is
    not a pose anyone can copy. On the real arm, folded all the way in means
    the gripper resting just off the base's right side.
    """
    from .arm_pose import BASE_RADIUS, POSES, ArmPoseView

    reach, height = tip_of(POSES["hover_over_min"])
    assert abs(reach) < 6, f"zero reach should be the base edge: {reach:.1f}"
    assert abs(height) < 6, height

    # Which is a real distance out from the centre, not zero.
    from_centre = reach + BASE_RADIUS / ArmPoseView._mm_to_units()
    assert from_centre > 35, from_centre


def test_the_reach_marks_start_at_the_base_edge_too() -> None:
    """The ruler and the arm have to agree: a 0 mark drawn at the centre
    while the arm measures from the rim is a drawing that lies by exactly
    one base radius at every point on it."""
    from .arm_pose import BASE_RADIUS, POSES, ArmPoseView

    view = ArmPoseView(POSES["hover_over_min"])
    assert view._mark(0) == BASE_RADIUS
    assert view._mark(120) == BASE_RADIUS + 120 * ArmPoseView._mm_to_units()


def test_every_pose_lands_on_its_own_reach_and_height() -> None:
    """The drawing has to agree with the numbers beside it.

    y is reach out from the base, z is height off the table — so the three
    table-level points sit *on* the line at 0, 60 and 120 mm, and the raised
    three sit on the 50 mm plane at 30, 75 and 120 mm. They were drawn
    swapped once: the z=0 row floating and the z=50 row sunk below the
    table, which made the two groups indistinguishable.
    """
    from .arm_pose import POSES

    for name, pose in POSES.items():
        reach, height = tip_of(pose)
        assert abs(reach - pose.distance) < 6, f"{name}: reach {reach:.1f}"
        assert abs(height - pose.height) < 6, f"{name}: height {height:.1f}"


def test_no_pose_draws_the_arm_through_the_table() -> None:
    """A drawing of the arm buried in the desk is not an instruction."""
    import math

    from .arm_pose import (
        FOREARM, GRIPPER, SHOULDER_HEIGHT, UPPER_ARM, ArmPoseView, POSES,
    )

    per_mm = ArmPoseView._mm_to_units()
    for name, pose in POSES.items():
        upper = math.radians(pose.elbow)
        elbow_z = SHOULDER_HEIGHT + math.cos(upper) * UPPER_ARM
        fore = upper + math.radians(180 - pose.wrist)
        wrist_z = elbow_z + math.cos(fore) * FOREARM
        tip_z = wrist_z + math.cos(fore) * GRIPPER
        assert min(elbow_z, wrist_z, tip_z) / per_mm > -2, name


def test_the_shapes_are_ordered_and_distinct() -> None:
    """Min, mid and max must actually differ, and in the right direction:
    a drawing that showed the same shape three times would be worse than
    none, because it would look authoritative."""
    from .arm_pose import POSES

    for suffix in ("over_min", "over_mid", "over_max"):
        assert f"hover_{suffix}" in POSES

    reach = [POSES[f"hover_over_{n}"].distance for n in ("min", "mid", "max")]
    assert reach == sorted(reach) and len(set(reach)) == 3, reach

    # The z=50 group is the same reach, lifted.
    for name in ("min_120", "mid_120", "max_120"):
        assert POSES[f"hover_{name}"].height == 50


def test_capture_records_the_arms_angles_not_the_sliders() -> None:
    """The measurement is where the arm *is*.

    A servo told 165 may sit at 162, and 162 is the number IK has to be
    built on — sending the slider value back would record the request and
    call it an observation.
    """
    from . import TWIST_ANGLE

    view, sent = ik_page()
    view._handle_reply({"__heartbeat__": {
        "ELBOW_ANGLE": 162, "WRIST_ANGLE": 88, "TWIST_ANGLE": 3,
    }})

    # Move a slider somewhere else entirely; it must not be what is saved.
    view._points[0]._sliders["elbow"].setValue(10)
    view._capture("hover_over_max", 120.0)

    _topic, payload = sent[-1]
    assert payload["calibration_type"] == "hover_over_max"
    assert payload["ELBOW"] == 162, payload
    assert payload["WRIST"] == 88
    assert payload["distance"] == 120.0
    # Twist is the exception: it is reported fixed, never observed. See below.
    assert payload["TWIST"] == TWIST_ANGLE


def test_twist_is_not_a_slider_on_this_page() -> None:
    """It turns the gripper without moving it, so it cannot change the reach
    or the height these six points measure. A control that cannot affect the
    measurement can only be set wrong."""
    view, _sent = ik_page()
    for point in view._points:
        assert "twist" not in point._sliders, point.calibration_type
        assert "twist" not in point._readouts, point.calibration_type
        assert set(point._sliders) == {"elbow", "wrist"}


def test_every_hover_point_reports_the_same_fixed_twist() -> None:
    """Fixed rather than observed, so six snapshots cannot disagree about it
    — a pose taken with the gripper accidentally rotated would otherwise
    bake that rotation into the calibration."""
    from . import TWIST_ANGLE

    view, sent = ik_page()
    view._handle_reply({"__heartbeat__": {
        "ELBOW_ANGLE": 100, "WRIST_ANGLE": 40, "TWIST_ANGLE": 17,
    }})

    for calibration_type in ("hover_over_min", "hover_mid_120"):
        view._capture(calibration_type, 60.0)
        _topic, payload = sent[-1]
        assert payload["TWIST"] == TWIST_ANGLE == 90, payload
        # And the arm's own twist reading is ignored rather than passed on.
        assert payload["TWIST"] != 17


def test_capture_is_a_filled_button_not_a_toolbar_action() -> None:
    """It writes a calibration to the robot. ToolbarAction is transparent by
    design — right in a dense bar, wrong for the one button under a form,
    where it read as decoration rather than the write it performs."""
    view, _sent = ik_page()
    for point in view._points:
        assert point.capture.objectName() == "SecondaryAction"


def test_nothing_is_captured_before_the_arm_reports_in() -> None:
    """With no heartbeat there is no pose to record, so Capture must not
    invent one from the sliders."""
    view, sent = ik_page()
    view._capture("hover_over_mid", 60.0)
    assert not sent


def test_jogging_drives_the_real_servo() -> None:
    """The sliders are a remote control, not a form: this is the half that
    gets the arm into the shape in the first place."""
    view, sent = ik_page()
    view._jog("ELBOW", 120)

    _topic, payload = sent[-1]
    assert payload["action"] == "servo"
    assert payload["servoName"] == "ELBOW"
    assert payload["position"] == 120


def test_heartbeats_cross_to_the_gui_thread_like_replies_do() -> None:
    """The heartbeat feeds the same widgets a command reply does, so it
    takes the same bridge — updating them from the network thread is what
    segfaulted a calibration run."""
    import threading

    from PySide6.QtCore import QTimer

    view, _sent = ik_page()
    handled: dict = {}
    original = view._handle_reply

    def spy(payload):
        handled["thread"] = threading.current_thread().name
        original(payload)
        _app.quit()

    view._bridge.arrived.disconnect()
    view._bridge.arrived.connect(spy)

    threading.Thread(
        target=lambda: view._on_heartbeat("black/HEARTBEAT", {
            "ELBOW_ANGLE": 55, "WRIST_ANGLE": 90, "TWIST_ANGLE": 0,
        }),
        name="paho-network", daemon=True,
    ).start()
    QTimer.singleShot(3000, _app.quit)
    _app.exec()

    assert handled.get("thread") == threading.main_thread().name, handled


def test_sliders_adopt_the_arm_once_then_stop_following() -> None:
    """Matching the arm on arrival is helpful; doing it forever would snap
    the handle out from under a dragging hand.

    Only the point being edited adopts — it is the one whose sliders can
    move — so the card is unlocked first.
    """
    view, _sent = ik_page()
    view._edit("hover_over_min")
    elbow = view._points[0]._sliders["elbow"]

    view._handle_reply({"__heartbeat__": {
        "ELBOW_ANGLE": 55, "WRIST_ANGLE": 90, "TWIST_ANGLE": 0,
    }})
    assert elbow.value() == 55

    view._handle_reply({"__heartbeat__": {
        "ELBOW_ANGLE": 120, "WRIST_ANGLE": 90, "TWIST_ANGLE": 0,
    }})
    assert elbow.value() == 55, "later heartbeats must not move the slider"


# ---- servo degrees are not screen degrees --------------------------------
def test_the_live_arm_is_not_drawn_before_two_captures() -> None:
    """The bug this whole mapping exists for.

    A heartbeat's ELBOW_ANGLE is a servo reading on a horn mounted however
    this robot's horn was mounted. Fed straight into the drawing it put the
    live arm 84 mm below the table while the real machine sat in the target
    shape — so with no mapping, nothing is drawn.
    """
    from .servo_frame import frame_from_captures

    assert not frame_from_captures({}).known
    assert not frame_from_captures(
        {"hover_over_min": {"elbow": 132, "wrist": 13}}
    ).known


def test_two_captures_recover_the_robots_own_mapping() -> None:
    """Two poses pin the line, and a third unseen pose lands on its mark."""
    import math

    from .arm_pose import (
        BASE_RADIUS, HAND, POSES, SHOULDER_HEIGHT, UNITS_PER_INCH, UPPER_ARM,
    )
    from .servo_frame import frame_from_captures

    # An inverted horn: servo = 180 - screen. A real and common mounting.
    captures = {
        key: {"elbow": 180 - POSES[key].elbow, "wrist": 180 - POSES[key].wrist}
        for key in ("hover_over_min", "hover_over_max")
    }
    frame = frame_from_captures(captures)
    assert frame.known

    # The pose that was *not* captured must now be placed correctly.
    pose = POSES["hover_over_mid"]
    elbow, wrist = frame.to_screen(180 - pose.elbow, 180 - pose.wrist)
    assert abs(elbow - pose.elbow) < 0.5, elbow
    assert abs(wrist - pose.wrist) < 0.5, wrist

    # And it must land on the table, not through it.
    upper = math.radians(elbow)
    x = math.sin(upper) * UPPER_ARM
    z = SHOULDER_HEIGHT + math.cos(upper) * UPPER_ARM
    fore = upper + math.radians(180 - wrist)
    x += math.sin(fore) * HAND
    z += math.cos(fore) * HAND
    per_mm = UNITS_PER_INCH / 25.4
    assert abs((x - BASE_RADIUS) / per_mm - 60) < 2, (x - BASE_RADIUS) / per_mm
    assert abs(z / per_mm) < 2, z / per_mm


def test_two_captures_of_the_same_shape_are_not_a_mapping() -> None:
    """Two readings degrees apart divide by almost nothing, and the fitted
    line throws the live arm off the widget. The user needs two genuinely
    different shapes."""
    from .servo_frame import frame_from_captures

    assert not frame_from_captures({
        "hover_over_min": {"elbow": 90, "wrist": 90},
        "hover_over_max": {"elbow": 91, "wrist": 91},
    }).known


def test_a_nonsense_fit_is_refused_rather_than_drawn() -> None:
    """Captures that disagree with their own target shapes produce a wild
    scale. Drawing it would be worse than drawing nothing."""
    from .servo_frame import frame_from_captures

    assert not frame_from_captures({
        "hover_over_min": {"elbow": 0, "wrist": 0},
        "hover_over_max": {"elbow": 180, "wrist": 1},
    }).wrist


def test_a_capture_is_filed_under_the_point_it_came_from() -> None:
    """Each capture must survive the next one.

    `on_completed` merges every reply into one flat dict, so the six poses
    would otherwise overwrite each other and only the last would remain —
    leaving nothing to fit a mapping to.
    """
    from . import page as ik_module
    from .. import step as step_module
    from . import CAPTURED_POINTS

    calibrations = store()
    view, _sent = ik_page()
    with mock.patch.object(step_module, "calibrations", lambda: calibrations), \
            mock.patch.object(ik_module, "calibrations", lambda: calibrations):
        view._handle_reply({"__heartbeat__": {
            "ELBOW_ANGLE": 132, "WRIST_ANGLE": 13, "TWIST_ANGLE": 90,
        }})
        view._capture("hover_over_min", 0.0)
        view.on_completed({"sender": "firmware", "status": "completed"})

        view._handle_reply({"__heartbeat__": {
            "ELBOW_ANGLE": 104, "WRIST_ANGLE": 62, "TWIST_ANGLE": 90,
        }})
        view._capture("hover_over_max", 120.0)
        view.on_completed({"sender": "firmware", "status": "completed"})

        values = calibrations.find("black", "ik").values

    points = values[CAPTURED_POINTS]
    assert points["hover_over_min"]["elbow"] == 132
    assert points["hover_over_min"]["wrist"] == 13
    assert points["hover_over_min"]["distance"] == 0.0
    assert points["hover_over_max"]["elbow"] == 104
    assert points["hover_over_max"]["wrist"] == 62
    assert points["hover_over_max"]["distance"] == 120.0

    # Nothing is stored globally for the step: a capture is one pose, and a
    # flat ELBOW/WRIST beside six of them would only ever hold the last one
    # while reading as the step's own value.
    assert set(values) == {CAPTURED_POINTS}, values


def test_capturing_one_pose_leaves_the_others_alone() -> None:
    """The report this came from: capturing Mid must not touch Min or Max."""
    from . import page as ik_module
    from .. import step as step_module
    from . import CAPTURED_POINTS

    calibrations = store()
    view, _sent = ik_page()
    with mock.patch.object(step_module, "calibrations", lambda: calibrations), \
            mock.patch.object(ik_module, "calibrations", lambda: calibrations):
        view._handle_reply({"__heartbeat__": {
            "ELBOW_ANGLE": 132, "WRIST_ANGLE": 13, "TWIST_ANGLE": 90,
        }})
        view._capture("hover_over_min", 0.0)
        view.on_completed({"sender": "firmware", "status": "completed"})

        # The arm moves, and a different point is captured.
        view._handle_reply({"__heartbeat__": {
            "ELBOW_ANGLE": 137, "WRIST_ANGLE": 59, "TWIST_ANGLE": 90,
        }})
        view._capture("hover_over_mid", 60.0)
        view.on_completed({"sender": "firmware", "status": "completed"})

        points = calibrations.find("black", "ik").values[CAPTURED_POINTS]

    assert points["hover_over_min"]["elbow"] == 132, "Min was overwritten"
    assert points["hover_over_mid"]["elbow"] == 137
    assert "hover_over_max" not in points, "an uncaptured point was invented"


# ---- each point's pin is its own -----------------------------------------
def test_every_point_pins_its_own_saved_value() -> None:
    """The contamination this came from.

    Six points, six saved angles, six different pins. Pinning anything
    shared — the live arm, say — gives every row the same mark, so capturing
    one point appears to move the mark on all the others.
    """
    from . import page as ik_module
    from .. import step as step_module

    calibrations = store()
    view, _sent = ik_page()
    with mock.patch.object(step_module, "calibrations", lambda: calibrations), \
            mock.patch.object(ik_module, "calibrations", lambda: calibrations):
        view._handle_reply({"__heartbeat__": {
            "ELBOW_ANGLE": 128, "WRIST_ANGLE": 4, "TWIST_ANGLE": 90,
        }})
        view._capture("hover_over_min", 0.0)
        view._handle_reply({
            "sender": "firmware", "action_id": "action-id", "status": "completed",
        })

        # A different shape, captured at a different angle.
        view._handle_reply({"__heartbeat__": {
            "ELBOW_ANGLE": 137, "WRIST_ANGLE": 59, "TWIST_ANGLE": 90,
        }})
        view._capture("hover_over_mid", 60.0)
        view._handle_reply({
            "sender": "firmware", "action_id": "action-id", "status": "completed",
        })

        pins = {
            point.calibration_type: point._sliders["elbow"].pin
            for point in view._points
        }

    assert pins["hover_over_min"] == 128
    assert pins["hover_over_mid"] == 137, "Mid took Min's pin"
    # The live arm is at 137, but an uncaptured point has saved nothing and
    # so has no pin at all.
    assert pins["hover_over_max"] is None, "an uncaptured point grew a pin"
    assert pins["hover_min_120"] is None


def test_an_uncaptured_point_has_nothing_to_snap_back_to() -> None:
    """No saved value means no pin, so nothing has drifted and the undo is
    not offered — it was appearing on every row against the live arm."""
    view, _sent = ik_page()
    view._handle_reply({"__heartbeat__": {
        "ELBOW_ANGLE": 128, "WRIST_ANGLE": 4, "TWIST_ANGLE": 90,
    }})

    point = view._points[2]          # Max, never captured
    slider = point._sliders["elbow"]
    assert slider.pin is None
    assert not slider.drifted

    slider.setValue(70)
    assert not slider.drifted, "drift needs a saved value to drift from"


def test_an_accidental_nudge_can_be_put_back() -> None:
    """A slider drives a real servo, so a nudge has already moved metal.
    Snapping back returns the handle to the saved angle and sends it."""
    from . import page as ik_module
    from .. import step as step_module

    calibrations = store()
    view, sent = ik_page()
    with mock.patch.object(step_module, "calibrations", lambda: calibrations), \
            mock.patch.object(ik_module, "calibrations", lambda: calibrations):
        view._handle_reply({"__heartbeat__": {
            "ELBOW_ANGLE": 128, "WRIST_ANGLE": 4, "TWIST_ANGLE": 90,
        }})
        view._capture("hover_over_min", 0.0)
        # The real reply path, so the in-place refresh runs and the pin is
        # set the way it is in the app.
        view._handle_reply({
            "sender": "firmware", "action_id": "action-id", "status": "completed",
        })

        slider = view._points[0]._sliders["elbow"]
        assert slider.pin == 128
        slider.setValue(70)
        assert slider.drifted

        sent.clear()
        slider.snap_back()

    assert slider.value() == 128
    assert not slider.drifted
    _topic, payload = sent[-1]
    assert payload["action"] == "servo"
    assert payload["servoName"] == "ELBOW"
    assert payload["position"] == 128, "snapping back must move the arm too"


def test_capturing_does_not_rebuild_the_other_points() -> None:
    """The page reload this came from.

    `send()` rebuilds to show a waiting card, which on this page destroys all
    six points and the slider positions the user posed by hand. Capturing one
    point must not visibly reload the other five.
    """
    from . import page as ik_module
    from .. import step as step_module

    calibrations = store()
    view, _sent = ik_page()
    with mock.patch.object(step_module, "calibrations", lambda: calibrations), \
            mock.patch.object(ik_module, "calibrations", lambda: calibrations):
        view._handle_reply({"__heartbeat__": {
            "ELBOW_ANGLE": 128, "WRIST_ANGLE": 4, "TWIST_ANGLE": 90,
        }})
        points = [id(point) for point in view._points]
        sliders = [id(point._sliders["elbow"]) for point in view._points]

        view._capture("hover_over_mid", 60.0)
        view.on_completed({"sender": "firmware", "status": "completed"})
        view.rebuild()

        assert [id(p) for p in view._points] == points, "points were rebuilt"
        assert [
            id(p._sliders["elbow"]) for p in view._points
        ] == sliders, "sliders were rebuilt"
        # The captured point still updates, in place.
        assert "Captured" in view._points[1].captured_label.text()


def test_going_back_to_a_saved_pose_drives_the_arm() -> None:
    """Two servo commands, not controlik.

    controlik asks the firmware to solve for a distance using the very
    calibration these captures are still building. The recorded angles are
    known good, so they are sent directly.
    """
    from . import page as ik_module
    from .. import step as step_module

    calibrations = store()
    view, sent = ik_page()
    with mock.patch.object(step_module, "calibrations", lambda: calibrations), \
            mock.patch.object(ik_module, "calibrations", lambda: calibrations):
        view._handle_reply({"__heartbeat__": {
            "ELBOW_ANGLE": 128, "WRIST_ANGLE": 4, "TWIST_ANGLE": 90,
        }})
        view._capture("hover_over_min", 0.0)
        view.on_completed({"sender": "firmware", "status": "completed"})

        # The arm wanders off somewhere else entirely.
        view._handle_reply({"__heartbeat__": {
            "ELBOW_ANGLE": 20, "WRIST_ANGLE": 150, "TWIST_ANGLE": 90,
        }})
        sent.clear()
        view._pose("hover_over_min")

    commands = [payload for _topic, payload in sent]
    assert [c["action"] for c in commands] == ["servo", "servo"], commands
    assert {c["servoName"]: c["position"] for c in commands} == {
        "ELBOW": 128, "WRIST": 4,
    }
    # The sliders follow, so they still say what would be sent next.
    point = view._points[0]
    assert point._sliders["elbow"].value() == 128
    assert point._sliders["wrist"].value() == 4


def test_going_back_needs_something_to_go_back_to() -> None:
    """An uncaptured point has no pose, so the button cannot be pressed and
    pressing it anyway sends nothing."""
    from . import page as ik_module
    from .. import step as step_module

    calibrations = store()
    view, sent = ik_page()
    with mock.patch.object(step_module, "calibrations", lambda: calibrations), \
            mock.patch.object(ik_module, "calibrations", lambda: calibrations):
        view._handle_reply({"__heartbeat__": {
            "ELBOW_ANGLE": 128, "WRIST_ANGLE": 4, "TWIST_ANGLE": 90,
        }})
        assert not view._points[0].pose_button.isEnabled()
        sent.clear()
        view._pose("hover_over_min")

    assert sent == []


def test_the_pin_is_not_the_sliders_own_colour() -> None:
    """The handle is a request and the pin is a measurement. Drawn in the
    accent — which is already the groove fill and the handle border — the
    pin reads as part of the slider's value rather than a separate fact."""
    from ....theme import stylesheet
    from ....theme.palette import DARK, LIGHT

    for palette in (LIGHT, DARK):
        _app.setStyleSheet(stylesheet(1.0, palette))
        view, _sent = ik_page()
        # Unlocked: a locked card greys the whole instrument, pin included.
        view._edit("hover_over_min")
        slider = view._points[0]._sliders["elbow"]
        slider.show()
        _app.processEvents()

        pin = slider.palette().windowText().color().name().lower()
        assert pin == palette.warn.lower(), (palette.name, pin)
        assert pin != palette.accent.lower(), palette.name


def test_a_locked_pose_is_greyed_out_entirely() -> None:
    """The filled groove is the loudest part of a slider. Muting only the
    handle left five locked cards still showing an accent bar, which read as
    five live controls on a page where only one can move the arm."""
    from ....theme import stylesheet
    from ....theme.palette import DARK

    _app.setStyleSheet(stylesheet(1.0, DARK))
    view, _sent = ik_page()
    view._edit("hover_over_min")
    widget = view.widget()
    widget.show()
    _app.processEvents()

    unlocked, locked = view._points[0], view._points[1]
    assert unlocked._sliders["elbow"].isEnabled()
    assert not locked._sliders["elbow"].isEnabled()

    # The pin follows the same rule: warn while live, muted while locked.
    live_pin = unlocked._sliders["elbow"].palette().windowText().color().name()
    dead_pin = locked._sliders["elbow"].palette().windowText().color().name()
    assert live_pin.lower() == DARK.warn.lower(), live_pin
    assert dead_pin.lower() == DARK.muted.lower(), dead_pin


def test_the_edit_toggle_says_what_it_does() -> None:
    """A word, not a glyph — the app's font stack is text faces, and ✎ fell
    through to a blank box. The label names the next action rather than the
    current state, so a checked button never reads "Edit"."""
    view, _sent = ik_page()
    toggle = view._points[1].edit_toggle

    assert toggle.text() == "Edit"
    view._edit("hover_over_mid")
    assert toggle.text() == "Done"
    view._edit("hover_over_mid")
    assert toggle.text() == "Edit"

    # Room reserved for the longer label, so the title row does not shift
    # as the text swaps between them.
    assert toggle.minimumWidth() >= toggle.sizeHint().width()


def test_no_control_is_ever_too_narrow_for_its_own_text() -> None:
    """A button clipped its label to "di".

    Fixed pixel widths were set against the font at zoom 1.0, so every zoom
    above it elided the middle of the text — on controls whose entire job is
    to say what they do, and on a readout that quietly reported the wrong
    angle. Nothing here may be narrower than the text it holds, at any zoom
    the View menu offers.
    """
    from ....menus.view import ZOOM_LEVELS
    from ....theme import stylesheet
    from ....theme.palette import DARK

    for scale in ZOOM_LEVELS:
        _app.setStyleSheet(stylesheet(scale, DARK))
        view, _sent = ik_page()
        widget = view.widget()
        widget.show()
        # The widest readings and the longest labels at once.
        view._handle_reply({"__heartbeat__": {
            "ELBOW_ANGLE": 180, "WRIST_ANGLE": 180, "TWIST_ANGLE": 90,
        }})
        view._edit("hover_over_mid")
        _app.processEvents()

        for point in view._points:
            controls = {
                "edit": point.edit_toggle,
                "capture": point.capture,
                "go to saved pose": point.pose_button,
                "reset": point._snap_backs["elbow"],
                "readout": point._readouts["elbow"],
            }
            for name, control in controls.items():
                assert control.width() >= control.sizeHint().width(), (
                    f"{name} clipped at zoom {scale}: "
                    f"{control.width()} < {control.sizeHint().width()}"
                )


def test_the_open_card_is_the_only_one_with_an_accent_border() -> None:
    """Which pose is live, said a third way — the toggle, the greyed
    sliders, and the border all agree."""
    view, _sent = ik_page()
    view._edit("hover_over_mid")

    editing = {
        point.calibration_type: point.property("editing")
        for point in view._points
    }
    assert editing["hover_over_mid"] is True
    assert all(
        value is False
        for key, value in editing.items() if key != "hover_over_mid"
    ), editing


# ---- one card at a time --------------------------------------------------
def test_nothing_is_editable_until_a_card_is_selected() -> None:
    """Six live forms are six ways to move one arm by accident."""
    view, _sent = ik_page()
    view._handle_reply({"__heartbeat__": {
        "ELBOW_ANGLE": 128, "WRIST_ANGLE": 4, "TWIST_ANGLE": 90,
    }})

    for point in view._points:
        assert not point._sliders["elbow"].isEnabled(), point.calibration_type
        assert not point.capture.isEnabled(), point.calibration_type
        assert not point.distance.isEnabled(), point.calibration_type
        assert not point.pose_button.isEnabled(), point.calibration_type
        # The way in is always available, or nothing could ever be unlocked.
        assert point.edit_toggle.isEnabled(), point.calibration_type


def test_unlocking_one_card_locks_every_other() -> None:
    view, _sent = ik_page()
    view._handle_reply({"__heartbeat__": {
        "ELBOW_ANGLE": 128, "WRIST_ANGLE": 4, "TWIST_ANGLE": 90,
    }})

    view._edit("hover_over_min")
    live = [p for p in view._points if p._sliders["elbow"].isEnabled()]
    assert [p.calibration_type for p in live] == ["hover_over_min"]

    # Moving to another card takes the first one's controls away.
    view._edit("hover_over_max")
    live = [p for p in view._points if p._sliders["elbow"].isEnabled()]
    assert [p.calibration_type for p in live] == ["hover_over_max"]
    assert view._points[0].capture.isEnabled() is False


def test_clicking_the_open_card_closes_it() -> None:
    """So the page can be put back to nothing-live."""
    view, _sent = ik_page()
    view._handle_reply({"__heartbeat__": {
        "ELBOW_ANGLE": 128, "WRIST_ANGLE": 4, "TWIST_ANGLE": 90,
    }})

    view._edit("hover_over_mid")
    assert view._points[1]._sliders["elbow"].isEnabled()

    view._edit("hover_over_mid")
    assert not view._points[1]._sliders["elbow"].isEnabled()
    assert view._editing == ""


def test_the_wheel_scrolls_the_page_and_never_a_servo() -> None:
    """The contamination this came from.

    Qt sends the wheel to whatever is under the pointer, so scrolling past a
    row drove its servo and rewrote its angle — silently, on points the user
    was not looking at. The event must pass through to the scroll area
    instead.
    """
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    view, sent = ik_page()
    view._edit("hover_over_min")
    view._handle_reply({"__heartbeat__": {
        "ELBOW_ANGLE": 128, "WRIST_ANGLE": 4, "TWIST_ANGLE": 90,
    }})

    slider = view._points[0]._sliders["elbow"]
    before = slider.value()
    sent.clear()

    event = QWheelEvent(
        QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, 120),
        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False,
    )
    slider.wheelEvent(event)

    assert slider.value() == before, "the wheel moved a slider"
    assert sent == [], "the wheel sent a servo command"
    assert not event.isAccepted(), "the page could not scroll"


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} IK tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
