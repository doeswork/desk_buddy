"""What a calibration step keeps out of the firmware's replies.

Every payload here was captured from a real ESP32 over MQTT, not invented —
the shapes the firmware actually sends are the whole point of these tests,
and a guessed one would pass while the robot kept disagreeing.

`StepPage` only: what one step does with a reply, and which thread it does it
on. The IK step's own behaviour lives beside it, in `ik/tests.py`.

    QT_QPA_PLATFORM=offscreen python -m studio.ui.workspaces.calibration.tests
    QT_QPA_PLATFORM=offscreen python -m studio.ui.workspaces.calibration.ik.tests
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ....models.config.calibrations import Calibrations
from ....storage.store import Store
from . import step as step_module
from .step import StepPage

_app = QApplication.instance() or QApplication([])

# Captured from the robot: `calibrate` with calibration_type perch_wrist_angle.
PERCH_REPLY = {
    "sender": "firmware",
    "action_id": "8bfdca7f",
    "status": "completed",
    "type": "perch_wrist_angle",
    "PERCH_WRIST_ANGLE": {"TYPE": "PERCH_WRIST_ANGLE", "VALUE": 91},
}

# Captured from the robot: `calibrate` with calibration_type hover_over_mid.
HOVER_REPLY = {
    "sender": "firmware",
    "action_id": "a5258e1f",
    "status": "completed",
    "hover_over_mid": {"ELBOW": 44, "WRIST": 90, "TWIST": 0, "DISTANCE": 120},
}


def page(step_key: str = "base_perch") -> StepPage:
    """A StepPage with just enough around it to record a result.

    Fully constructed rather than `__new__`-ed: `__init__` is where the
    reply bridge is built, and building it on this thread is what makes the
    signal a queued connection.
    """
    view = StepPage(type("Workspace", (), {"robot": "black"})())
    view.step_key = step_key
    return view


def store() -> Calibrations:
    return Calibrations(Store("calibrations", directory=Path(tempfile.mkdtemp())))


def test_a_single_measurement_is_unwrapped() -> None:
    """The firmware reports one written value as {"TYPE": …, "VALUE": n}.

    Kept whole, the saved-values card renders a literal Python dict where a
    number belongs.
    """
    assert page().results(PERCH_REPLY) == {"PERCH_WRIST_ANGLE": 91}


def test_a_group_of_values_is_flattened_not_unwrapped() -> None:
    """A hover reply is several real values, not one measurement — there is
    no VALUE key to unwrap, so every angle is kept."""
    assert page().results(HOVER_REPLY) == {
        "ELBOW": 44, "WRIST": 90, "TWIST": 0, "DISTANCE": 120,
    }


def test_envelope_fields_are_never_saved_as_results() -> None:
    """action_id and friends identify the exchange, not the robot."""
    found = page().results(PERCH_REPLY)
    for field in ("sender", "action_id", "status", "type"):
        assert field not in found, field


def test_several_commands_in_one_step_accumulate() -> None:
    """Base + Perch sends six commands, one per field, and each reply
    carries only the value it just wrote. Replacing rather than merging
    would leave the last one as the only one saved — which looks exactly
    like the other five having failed."""
    calibrations = store()
    view = page()

    with mock.patch.object(step_module, "calibrations", lambda: calibrations):
        for field, value in (
            ("PERCH_ELBOW_ANGLE", 44),
            ("PERCH_WRIST_ANGLE", 91),
            ("PERCH_TWIST_ANGLE", 0),
        ):
            view.on_completed({
                "sender": "firmware", "action_id": "x", "status": "completed",
                field: {"TYPE": field, "VALUE": value},
            })

    saved = calibrations.find("black", "base_perch")
    assert saved.values == {
        "PERCH_ELBOW_ANGLE": 44,
        "PERCH_WRIST_ANGLE": 91,
        "PERCH_TWIST_ANGLE": 0,
    }


def test_re_sending_one_field_updates_only_that_field() -> None:
    """Correcting one value must not discard the other five."""
    calibrations = store()
    view = page()

    with mock.patch.object(step_module, "calibrations", lambda: calibrations):
        view.on_completed({
            "sender": "firmware", "action_id": "x", "status": "completed",
            "PERCH_ELBOW_ANGLE": {"TYPE": "PERCH_ELBOW_ANGLE", "VALUE": 44},
        })
        view.on_completed({
            "sender": "firmware", "action_id": "y", "status": "completed",
            "PERCH_ELBOW_ANGLE": {"TYPE": "PERCH_ELBOW_ANGLE", "VALUE": 50},
        })
        view.on_completed({
            "sender": "firmware", "action_id": "z", "status": "completed",
            "PERCH_WRIST_ANGLE": {"TYPE": "PERCH_WRIST_ANGLE", "VALUE": 91},
        })

    saved = calibrations.find("black", "base_perch")
    assert saved.values == {"PERCH_ELBOW_ANGLE": 50, "PERCH_WRIST_ANGLE": 91}


def test_commands_go_out_as_studio_not_as_the_robot() -> None:
    """Calibration speaks with Studio's own credential, which the ACL gives
    the whole topic tree. A robot's account reaches only its own subtree, so
    sending as one would be refused the moment a step addressed another."""
    from ....services.network.pub_sub import calibration_messages

    sent = {}

    class FakeClient:
        def publish(self, topic, payload, **_kwargs):
            sent["topic"] = topic
            sent["payload"] = payload
            return "action-id"

    calibration_messages.send_perch_value(FakeClient(), "black", "wrist", 91)

    # The command is addressed to the robot's topic...
    assert sent["topic"] == "black/commands"
    # ...but nothing in it claims to *be* the robot: MqttClient.publish fills
    # in the sender, and the connection itself authenticates as studio.
    assert sent["payload"]["sender"] != "black"
    assert sent["payload"]["action"] == "calibrate"
    assert sent["payload"]["calibration_type"] == "perch_wrist_angle"
    assert sent["payload"]["value"] == 91


# ---- Replies arrive on the wrong thread ----------------------------------
# paho runs on_message on its own network thread. Every reply here ends in
# rebuild(), which deletes and constructs widgets — illegal off the GUI
# thread, and not merely in theory: it segfaulted the app mid-layout during
# a base-rotation run, with the main thread inside QBoxLayout::setGeometry.


def test_a_reply_is_handled_on_the_gui_thread() -> None:
    """The fix, asserted directly: emitted from a worker, handled on main."""
    import threading

    from PySide6.QtCore import QTimer

    view = page()
    handled: dict = {}

    view._bridge.arrived.disconnect()
    view._bridge.arrived.connect(
        lambda payload: (
            handled.update(thread=threading.current_thread().name),
            _app.quit(),
        )
    )

    def worker() -> None:
        # Exactly how paho calls a subscriber.
        view._on_reply("black/events", {"action_id": "abc", "status": "progress"})

    threading.Thread(target=worker, name="paho-network", daemon=True).start()
    QTimer.singleShot(3000, _app.quit)
    _app.exec()

    assert handled.get("thread") == threading.main_thread().name, handled


def test_the_network_callback_only_hands_over() -> None:
    """`_on_reply` must stay free of anything that touches a widget.

    The guard is that it does not consult page state either: reading
    `_pending` here and rebuilding there would put half the decision on the
    wrong thread again.
    """
    view = page()
    emitted = []
    view._bridge.arrived.disconnect()
    view._bridge.arrived.connect(emitted.append)

    view._pending = "something-else"
    view._on_reply("black/events", {"action_id": "abc", "status": "progress"})

    # Handed over regardless of whether it matches — filtering is the GUI
    # thread's job, in _handle_reply.
    assert emitted == [{"action_id": "abc", "status": "progress"}]


def test_a_reply_for_a_replaced_command_is_ignored() -> None:
    """A queued reply can outlive the request it belongs to: unsubscribing
    does not recall what is already on the event queue."""
    calibrations = store()
    view = page()
    view._pending = ""          # the wait was cancelled

    with mock.patch.object(step_module, "calibrations", lambda: calibrations):
        view._handle_reply({
            "sender": "firmware", "action_id": "stale", "status": "completed",
            "PERCH_ELBOW_ANGLE": {"TYPE": "PERCH_ELBOW_ANGLE", "VALUE": 44},
        })

    assert calibrations.find("black", "base_perch") is None




# ---- the shared controls -------------------------------------------------
def _wheel():
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    return QWheelEvent(
        QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, 120),
        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False,
    )


def test_no_calibration_spin_box_answers_the_wheel() -> None:
    """Every page in this workspace writes to a physical arm.

    Qt sends the wheel to whatever is under the pointer, so a plain
    QDoubleSpinBox on a tall form changes its value — and drives the
    hardware — while the user is only scrolling past it. The shared control
    is what stops that, so every page has to actually use it.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).parent
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path.name in ("controls.py", "tests.py"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Name) and node.id in (
                "QDoubleSpinBox", "QSpinBox", "QSlider",
            ):
                offenders.append(f"{path.relative_to(root)}:{node.lineno} {node.id}")

    assert not offenders, (
        "use calibration.controls instead of a raw Qt input: " + ", ".join(offenders)
    )


def test_a_scroll_safe_spin_box_keeps_its_value() -> None:
    from .controls import ScrollSafeSpinBox

    spin = ScrollSafeSpinBox()
    spin.setRange(0, 180)
    spin.setValue(90)

    event = _wheel()
    spin.wheelEvent(event)

    assert spin.value() == 90
    assert not event.isAccepted(), "the page could not scroll"


def test_widest_sizes_a_control_to_its_longest_label() -> None:
    """The replacement for setFixedWidth. Asked of the widget, so the
    stylesheet's own padding is counted rather than guessed at."""
    from PySide6.QtWidgets import QPushButton

    from .controls import widest

    button = QPushButton("Edit")
    needed = widest(button, "Edit", "Done", "Something much longer")

    button.setText("Something much longer")
    assert needed >= button.sizeHint().width()
    # The caller's text survives being measured.
    button.setText("Edit")
    assert widest(button, "Done") and button.text() == "Edit"


def _visual_page():
    """A Visual page with a robot selected and a stub client."""
    from .visual import VisualPage

    sent = []

    class FakeClient:
        def publish(self, topic, payload, **_kwargs):
            sent.append((topic, payload))
            return "action-id"

        def subscribe(self, _topic, _callback):
            return lambda: None

        def subscribe_raw(self, _topic, _callback):
            return lambda: None

    workspace = type("Workspace", (), {
        "robot": "black",
        "client": lambda self=None: FakeClient(),
        "robots": lambda self=None: [],
    })()
    view = VisualPage(workspace)
    view.widget()
    return view, sent


def test_the_perch_is_posed_with_sliders_and_measured_with_numbers() -> None:
    """A camera pose is found by moving the arm and watching the frame, so
    the angles are sliders. A mark on the table is measured with a ruler and
    typed, so the reaches are not."""
    from .controls import PinnedSlider, ScrollSafeSpinBox
    from .visual import ANGLES, REACHES

    view, _sent = _visual_page()
    form = view._form

    for key, _joint, label in ANGLES:
        assert isinstance(form._sliders[key], PinnedSlider), label
    for key, label, _default in REACHES:
        assert isinstance(form._reaches[key], ScrollSafeSpinBox), label


def test_the_perch_moved_off_the_base_page() -> None:
    """A perch is a camera pose, not a property of the turntable. Setting it
    on a page with no picture on it was guesswork."""
    from .base_perch import FIELDS

    assert FIELDS == (), "the base page still owns perch fields"


def test_aiming_a_joint_drives_the_real_servo() -> None:
    """The camera rides on the arm, so pointing it means moving it — and
    that has to happen while the user drags, not only on save."""
    view, sent = _visual_page()
    view._form._sliders["elbow"].committed.emit(45)

    _topic, body = sent[-1]
    assert body["action"] == "servo"
    assert body["servoName"] == "ELBOW"
    assert body["position"] == 45


def test_saving_the_perch_sends_all_three_angles() -> None:
    """The firmware stores three, even though only two are aimed.

    Twist has no slider — it cannot move the camera — but leaving it unsent
    would keep whatever an earlier calibration wrote, so the saved perch
    would not be the pose on screen.
    """
    from .visual import ANGLES, TWIST_NEUTRAL

    view, sent = _visual_page()
    form = view._form
    for key, _joint, _label in ANGLES:
        form._sliders[key].setValue(100)

    form.save_perch.click()

    written = {
        body.get("calibration_type"): body.get("value")
        for _topic, body in sent
    }
    assert written == {
        "perch_elbow_angle": 100.0,
        "perch_wrist_angle": 100.0,
        "perch_twist_angle": float(TWIST_NEUTRAL),
    }, written


def test_twist_is_not_a_slider_on_this_page() -> None:
    """It turns the gripper about its own axis, which cannot change where
    the camera points. A control that cannot move what is being set can only
    be set wrong — the same reason it is absent from the IK page."""
    view, _sent = _visual_page()
    assert set(view._form._sliders) == {"elbow", "wrist"}


def test_the_base_can_be_sent_to_true_north() -> None:
    """A perch is only meaningful from a known heading: the same arm pose
    facing two ways sees two different tables."""
    view, sent = _visual_page()
    view._form.home.click()

    _topic, body = sent[-1]
    assert body["action"] == "baseRotate"
    assert body["controlType"] == "HOME"
    assert body["direction"] in ("LEFT", "RIGHT")


def test_the_handles_follow_the_arm_until_they_are_touched() -> None:
    """This page is for finding a pose, so a handle nobody has moved starts
    where the arm already is. `drifted` cannot decide that — an untouched
    slider sits at its default and is already drifted from the real angle."""
    view, _sent = _visual_page()
    form = view._form

    form.set_live({"ELBOW_ANGLE": 137, "WRIST_ANGLE": 59, "TWIST_ANGLE": 90})
    assert form._sliders["elbow"].value() == 137
    assert form._sliders["elbow"].pin == 137

    # Once driven by hand, the handle is the user's. A real drag moves the
    # handle and then commits on release; both together are what a touch is.
    form._sliders["elbow"].setValue(45)
    form._sliders["elbow"].committed.emit(45)
    form.set_live({"ELBOW_ANGLE": 137, "WRIST_ANGLE": 59, "TWIST_ANGLE": 90})
    assert form._sliders["elbow"].value() == 45, "the arm took the handle back"
    assert form._sliders["elbow"].pin == 137


# ---- a step's form outlives its replies ----------------------------------
# Three pages hit this in turn: IK's captures, IK's per-point state, and
# Base + Perch's sliders. Each was patched where it showed up, which left the
# next page with stateful controls to discover it again. The guarantee belongs
# to StepPage, so these test it there.


class _FormPage(StepPage):
    """A step whose form holds something the user typed."""

    step_key = "base_perch"

    def __init__(self, workspace=None) -> None:
        super().__init__(workspace)
        self.builds = 0
        self.refreshes = 0

    def build_form(self):
        from PySide6.QtWidgets import QLineEdit

        self.builds += 1
        field = QLineEdit()
        field.setObjectName("KeptField")
        return field

    def form_refreshed(self, form) -> None:
        self.refreshes += 1
        super().form_refreshed(form)


def _form_page(robot: str = "black") -> _FormPage:
    class FakeClient:
        def publish(self, _topic, _payload, **_kwargs):
            return "action-id"

        def subscribe(self, _topic, _callback):
            return lambda: None

    workspace = type("Workspace", (), {
        "robot": robot,
        "client": lambda self=None: FakeClient(),
        "robots": lambda self=None: [],
    })()
    view = _FormPage(workspace)
    view.widget()
    return view


def test_a_form_survives_the_reply_to_its_own_command() -> None:
    """The trap, stated once: a step that sends and then rebuilds must not
    destroy the controls the user was working in."""
    view = _form_page()
    field = view._form
    field.setText("half typed")

    view.send("action-id", waiting_text="Setting…")
    assert view._form is field, "the form was rebuilt while waiting"
    assert view._form.text() == "half typed"

    view._handle_reply({
        "sender": "firmware", "action_id": "action-id", "status": "completed",
    })
    assert view._form is field, "the form was rebuilt on the reply"
    assert view._form.text() == "half typed"
    assert view.builds == 1, f"built {view.builds} times"


def _waiting_shown(view) -> bool:
    from PySide6.QtWidgets import QLabel

    return any(
        "Waiting for the robot" in label.text()
        for label in view.built().widget().findChildren(QLabel)
    )


def _pump(milliseconds: int) -> None:
    """Let Qt's timers run for a moment, without blocking the event loop."""
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def test_a_quick_reply_never_shows_a_waiting_card() -> None:
    """A robot on the LAN answers a servo command in milliseconds, so the
    card appeared and vanished between two frames — flashing under the hand
    of anyone dragging a slider that sends on release."""
    view = _form_page()
    view.send("action-id", waiting_text="Setting elbow…")
    assert not _waiting_shown(view), "card shown before the grace period"

    view._handle_reply({
        "sender": "firmware", "action_id": "action-id", "status": "completed",
    })
    _pump(view.WAITING_GRACE_MS + 200)
    assert not _waiting_shown(view), "card appeared after the reply landed"


def test_a_robot_that_does_not_answer_is_reported() -> None:
    """The card still exists — silence has to be visible, or the page just
    looks broken."""
    view = _form_page()
    view.send("action-id", waiting_text="Setting elbow…")

    _pump(view.WAITING_GRACE_MS + 200)
    assert _waiting_shown(view), "a stalled command said nothing"


def test_progress_shows_the_wait_at_once() -> None:
    """A robot reporting progress is saying this will take a while — the
    base rotation profile runs for minutes. No grace period for that."""
    view = _form_page()
    view.send("action-id", waiting_text="Measuring…")
    view._handle_reply({
        "sender": "firmware", "action_id": "action-id",
        "status": "progress", "progress": "pass 1 of 2",
    })

    assert _waiting_shown(view)
    assert view._waiting_text == "pass 1 of 2"


def test_waiting_is_shown_beside_the_form_not_instead_of_it() -> None:
    """The form used to be swapped out for the waiting card, which is what
    made every reply destructive. Both are on screen now."""
    view = _form_page()
    view.send("action-id", waiting_text="Setting elbow…")
    _pump(view.WAITING_GRACE_MS + 200)

    from PySide6.QtWidgets import QLabel, QLineEdit

    body = view.built().widget()
    assert body.findChild(QLineEdit, "KeptField") is not None, "form vanished"
    assert any(
        "Setting elbow…" in label.text() for label in body.findChildren(QLabel)
    ), "no waiting card"


def test_a_kept_form_is_refreshed_on_every_rebuild() -> None:
    """Kept is not stale: the page gets a hook to bring it up to date."""
    view = _form_page()
    before = view.refreshes

    view.rebuild()
    view.rebuild()

    assert view.refreshes == before + 2
    assert view.builds == 1


def test_switching_robots_discards_the_form() -> None:
    """A form is built from one robot's saved values, so keeping it across a
    switch would leave another machine's numbers on screen."""
    view = _form_page()
    view._form.setText("black's value")

    view.workspace.robot = "white"
    view.rebuild()

    assert view.builds == 2, "the form was kept across a robot change"
    assert view._form.text() == ""


def test_a_page_may_opt_out_of_keeping_its_form() -> None:
    """Escape hatch for a form rebuilt wholly from the reply, with nothing
    of the user's in it."""
    view = _form_page()
    view.form_is_disposable = True

    view.rebuild()
    view.rebuild()

    assert view.builds == 3, "disposable forms should be rebuilt each time"


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} step tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
