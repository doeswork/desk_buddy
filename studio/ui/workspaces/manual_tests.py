"""Manual Controller UI and MQTT command contract.

    QT_QPA_PLATFORM=offscreen python -m studio.ui.workspaces.manual_tests
"""

from __future__ import annotations

import os
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QPushButton, QSlider

from ...models.config.robots import Robot
from ...services.network.pub_sub import manual_messages
from . import manual as manual_module
from .manual import ManualWorkspace

_app = QApplication.instance() or QApplication([])


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


def workspace(entries=(Robot("black", "Black Buddy"),)) -> tuple[ManualWorkspace, RecordingClient]:
    registry = Registry(entries)
    client = RecordingClient()
    patcher = mock.patch.object(manual_module, "robots", lambda: registry)
    patcher.start()
    space = ManualWorkspace()
    space.client = lambda: client
    # Keep the patch alive for every later property read on this workspace.
    space._test_registry_patch = patcher
    return space, client


def close_workspace(space: ManualWorkspace) -> None:
    space._test_registry_patch.stop()


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


def test_workspace_uses_studios_shared_authenticated_client() -> None:
    registry = Registry((Robot("black", "Black Buddy"),))
    client = RecordingClient()
    with mock.patch.object(manual_module, "robots", lambda: registry), \
            mock.patch.object(manual_module, "mqtt_client", lambda: client):
        found = ManualWorkspace().client()

    assert found is client
    assert client.reconciled


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


def test_page_has_the_legacy_sliders_and_quick_actions() -> None:
    space, _client = workspace()
    try:
        widget = space.page.widget()
        sliders = widget.findChildren(QSlider)
        assert [slider.accessibleName() for slider in sliders] == [
            "Elbow", "Wrist", "Twist", "Distance",
        ]
        assert [(slider.minimum(), slider.maximum()) for slider in sliders] == [
            (0, 180), (0, 180), (0, 180), (1, 120),
        ]

        buttons = {button.text() for button in widget.findChildren(QPushButton)}
        assert {
            "← Left", "Right →", "Home base", "Grab", "Soft hold",
            "Drop", "Perch", "Take photo",
        } <= buttons

        picker = next(
            combo for combo in widget.findChildren(QComboBox)
            if combo.accessibleName() == "Target robot"
        )
        assert picker.currentText() == "Black Buddy"
    finally:
        close_workspace(space)


def test_slider_previews_without_publishing_then_sends_on_commit() -> None:
    space, client = workspace()
    try:
        widget = space.page.widget()
        elbow = next(
            slider for slider in widget.findChildren(QSlider)
            if slider.accessibleName() == "Elbow"
        )
        elbow.setValue(126)
        assert client.sent == [], "previewing a drag must not flood MQTT"

        elbow.committed.emit(126)
        topic, payload = client.sent[-1]
        assert topic == "black/test"
        assert payload["sender"] == "studio"
        assert payload["servoName"] == "ELBOW"
        assert payload["position"] == 126
    finally:
        close_workspace(space)


def test_no_robot_leaves_the_controller_visible_but_disabled() -> None:
    space, _client = workspace(entries=())
    try:
        widget = space.page.widget()
        assert all(not slider.isEnabled() for slider in widget.findChildren(QSlider))
        assert all(not action.clickable for action in space.build_actions()
                   if hasattr(action, "clickable"))
        assert "No robot selected" == space.status
    finally:
        close_workspace(space)


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} manual controller tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
