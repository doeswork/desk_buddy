"""What the Manual Controller sends, and when it refuses to send anything.

No Qt: the point of the service is that these questions can be asked without
a window. The widgets that ask them are tested in
`studio/ui/components/manual_control_tests.py`.

    python -m studio.services.manual_controller.tests
"""

from __future__ import annotations

from unittest import mock

from ...models.config.robots import Robot
from ..network.pub_sub import manual_messages
from . import live_actions
from .live_actions import LiveActions


class RecordingClient:
    def __init__(self, *, running: bool = True) -> None:
        self.running = running
        self.status = "Connected" if running else "Broker is not running"
        self.sent: list[tuple[str, dict]] = []
        self.reconciled = False

    def reconcile(self) -> None:
        self.reconciled = True

    def publish(self, topic: str, payload: dict, **_kwargs) -> str:
        body = dict(payload)
        body.setdefault("sender", "studio")
        body.setdefault("action_id", f"action-{len(self.sent) + 1}")
        self.sent.append((topic, body))
        return str(body["action_id"])


class Registry:
    def __init__(self, entries=()) -> None:
        self.entries = list(entries)

    def all(self) -> list[Robot]:
        return list(self.entries)

    def find(self, name: str) -> Robot | None:
        return next((robot for robot in self.entries if robot.name == name), None)


def actions(entries=(Robot("black", "Black Buddy"),), *, running: bool = True):
    """A LiveActions on a fake registry, with what it said and a fake client."""
    registry = Registry(entries)
    client = RecordingClient(running=running)
    said: list[str] = []
    patcher = mock.patch.object(live_actions, "robots", lambda: registry)
    patcher.start()
    live = LiveActions(announce=said.append)
    live.client = lambda: client
    live._test_patch = patcher
    return live, client, said, registry


def close(live: LiveActions) -> None:
    live._test_patch.stop()


def test_every_manual_message_is_addressed_as_studio() -> None:
    client = RecordingClient()

    manual_messages.send_servo(client, "black", "ELBOW", 123)
    manual_messages.send_ik(client, "black", 60)
    manual_messages.send_gripper(client, "black", "SOFTHOLD")
    manual_messages.send_base_steps(client, "black", "LEFT", 3, "slow")
    manual_messages.send_base_home(client, "black")
    manual_messages.send_perch(client, "black")
    manual_messages.send_photo(client, "black")

    assert len(client.sent) == 7
    for topic, payload in client.sent:
        assert topic == "black/test"
        assert payload["sender"] == "studio"


def test_joint_slider_keeps_the_legacy_live_drive_contract() -> None:
    client = RecordingClient()
    action_id = manual_messages.send_servo(client, "black", "wrist", 117)
    _topic, payload = client.sent[-1]
    assert action_id == "live"
    assert payload == {
        "sender": "studio",
        "action": "servo",
        "action_id": "live",
        "servoName": "WRIST",
        "position": 117,
    }


def test_base_controls_use_the_firmwares_current_encoder_contract() -> None:
    client = RecordingClient()
    manual_messages.send_base_steps(client, "black", "right", 12, "regular")
    _topic, payload = client.sent[-1]
    assert payload["action"] == "baseRotate"
    assert payload["controlType"] == "ENCODER"
    assert payload["direction"] == "RIGHT"
    assert payload["value"] == 12
    assert payload["speed"] == "regular"


def test_it_uses_studios_shared_authenticated_client() -> None:
    registry = Registry((Robot("black", "Black Buddy"),))
    client = RecordingClient()
    with mock.patch.object(live_actions, "robots", lambda: registry), \
            mock.patch.object(live_actions, "mqtt_client", lambda: client):
        found = LiveActions().client()

    assert found is client
    assert client.reconciled


def test_every_command_reaches_the_selected_robot() -> None:
    """One call per verb: the eight the controller offers all publish."""
    live, client, _said, _registry = actions()
    try:
        live.servo("ELBOW", 100)
        live.reach(60)
        live.rotate("LEFT", 3, "slow")
        live.home()
        live.gripper("GRAB")
        live.perch()
        live.photo()

        assert len(client.sent) == 7
        assert {topic for topic, _payload in client.sent} == {"black/test"}
    finally:
        close(live)


def test_no_robot_says_where_to_make_one_and_sends_nothing() -> None:
    """A refusal the user can act on, not a silent no-op."""
    live, client, said, _registry = actions(entries=())
    try:
        assert live.photo() == ""
        assert client.sent == []
        assert said == ["Mark a user as a robot on Network → Robots first."]
    finally:
        close(live)


def test_a_broker_that_is_down_is_reported_in_its_own_words() -> None:
    """The client already knows why it cannot publish; repeat that."""
    live, client, said, _registry = actions(running=False)
    try:
        assert live.home() == ""
        assert client.sent == []
        assert said == ["Broker is not running"]
    finally:
        close(live)


def test_a_sent_command_names_the_robot_it_went_to() -> None:
    live, _client, said, _registry = actions()
    try:
        action_id = live.rotate("LEFT", 1, "slow")
        assert action_id
        assert said == ["Sent base left 1 step to Black Buddy as Studio."]
    finally:
        close(live)


def test_a_deleted_robot_falls_back_rather_than_publishing_into_the_void() -> None:
    """A selection can outlive the robot; the topic must not."""
    live, _client, _said, registry = actions(
        entries=(Robot("black", "Black Buddy"), Robot("white", "White Buddy"))
    )
    try:
        live.select_robot("white")
        assert live.robot == "white"

        registry.entries = [Robot("black", "Black Buddy")]
        assert live.robot == "black"
    finally:
        close(live)


def test_robots_changed_reports_a_rename_once() -> None:
    """A robot renamed elsewhere is one the picker is spelling wrong."""
    live, _client, _said, registry = actions()
    try:
        assert live.robots_changed() is False

        registry.entries = [Robot("black", "Night Buddy")]
        assert live.robots_changed() is True
        assert live.robots_changed() is False, "the change was already taken"
    finally:
        close(live)


def test_selecting_a_robot_asks_the_caller_to_redraw() -> None:
    """The service does not know the selection has a picker showing it."""
    registry = Registry((Robot("black", "Black"), Robot("white", "White")))
    redraws: list[int] = []
    with mock.patch.object(live_actions, "robots", lambda: registry):
        live = LiveActions(refresh=lambda: redraws.append(1))
        live.select_robot("white")
        assert redraws == [1]

        live.select_robot("white")
        assert redraws == [1], "re-picking the current robot is not a change"


def test_it_works_with_no_callbacks_at_all() -> None:
    """A script has no status strip and nothing to redraw."""
    registry = Registry(())
    with mock.patch.object(live_actions, "robots", lambda: registry):
        assert LiveActions().photo() == ""


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} manual controller service tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
