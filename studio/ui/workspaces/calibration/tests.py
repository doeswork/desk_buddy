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




def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} step tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
