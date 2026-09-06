"""What the Serial Monitor makes of the robot's replies.

The robot answers every provisioning command on the same port it received it
on. Reading that answer is the difference between "Studio wrote some bytes"
and "the robot saved the settings" — a distinction that cost a debugging
session when a rejected MQTT command was reported as a successful send.

    QT_QPA_PLATFORM=offscreen python -m studio.ui.components.serial_tests
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ...models.config.mqtt_users import STUDIO_NAME
from .serial_monitor import CUSTOM_ACCOUNT, MqttDialog, SerialMonitor

_app = QApplication.instance() or QApplication([])


def monitor() -> SerialMonitor:
    return SerialMonitor()


def feed(view: SerialMonitor, payload: dict) -> None:
    """Deliver one JSON line exactly as the serial port would."""
    view._inspect_protocol_lines(json.dumps(payload) + "\n")


# ---- Rejections the robot reports ---------------------------------------

def test_a_malformed_command_is_reported() -> None:
    """SerialProvisioning.cpp answers kind "error", not "mqtt".

    This is the regression that mattered: a robot replying "Command must be
    valid JSON" was filtered out by a check for ("wifi", "mqtt"), so Studio
    showed a successful send while the robot had saved nothing at all.
    """
    view = monitor()
    feed(view, {
        "serial_provisioning": "error",
        "status": "error",
        "error": "Command must be valid JSON",
    })
    assert "failed" in view.status.text()
    assert "Command must be valid JSON" in view.status.text()


def test_an_oversized_command_is_reported() -> None:
    view = monitor()
    feed(view, {
        "serial_provisioning": "error",
        "status": "error",
        "error": "Serial command was too large",
    })
    assert "too large" in view.status.text()


def test_an_unknown_command_is_reported() -> None:
    view = monitor()
    feed(view, {
        "serial_provisioning": "error",
        "status": "error",
        "error": "Unknown serial provisioning command",
    })
    assert "failed" in view.status.text()


def test_a_rejection_also_reaches_the_log() -> None:
    """The status bar is one line that the next message overwrites.

    A rejection belongs where the user is already reading the robot's own
    output, not only in a label a heartbeat tick will sit beside.
    """
    view = monitor()
    feed(view, {
        "serial_provisioning": "mqtt",
        "status": "error",
        "error": "port must be between 1 and 65535",
    })
    assert "ROBOT REJECTED" in view.output.toPlainText()
    assert "port must be between 1 and 65535" in view.output.toPlainText()


def test_each_kind_of_rejection_is_labelled() -> None:
    view = monitor()
    feed(view, {"serial_provisioning": "wifi", "status": "error",
                "error": "SSID must be 1-32 bytes"})
    assert "Wi-Fi" in view.status.text()

    feed(view, {"serial_provisioning": "mqtt", "status": "error",
                "error": "server is required"})
    assert "MQTT settings" in view.status.text()


# ---- Successes ----------------------------------------------------------

def test_a_saved_command_is_reported() -> None:
    view = monitor()
    feed(view, {
        "serial_provisioning": "mqtt",
        "status": "saved",
        "server": "192.168.16.110",
    })
    assert "saved" in view.status.text()
    assert "failed" not in view.status.text()
    assert "ROBOT REJECTED" not in view.output.toPlainText()


# ---- Everything else on the wire ----------------------------------------

def test_heartbeats_are_not_mistaken_for_provisioning() -> None:
    view = monitor()
    feed(view, {
        "serial_heartbeat": True,
        "uptime_ms": 1234,
        "wifi_connected": True,
        "free_heap": 240000,
    })
    assert "failed" not in view.status.text()
    assert view._last_heartbeat is not None


def test_plain_firmware_output_is_ignored() -> None:
    """Most of what the robot prints is not JSON, and must not error."""
    view = monitor()
    view._inspect_protocol_lines(
        "=== DeskBuddy Starting ===\nConnecting MQTT (TLS)… failed, rc=5\n"
    )
    assert "failed:" not in view.status.text()


def test_a_line_split_across_reads_is_still_parsed() -> None:
    """Serial delivers arbitrary chunks, not whole lines."""
    view = monitor()
    payload = json.dumps({
        "serial_provisioning": "error",
        "status": "error",
        "error": "Command must be valid JSON",
    })
    view._inspect_protocol_lines(payload[:20])
    assert "failed" not in view.status.text()
    view._inspect_protocol_lines(payload[20:] + "\n")
    assert "Command must be valid JSON" in view.status.text()


# ---- Getting the whole command onto the wire ----------------------------

class ChunkedPort:
    """A port that accepts at most `chunk` bytes per write.

    Exactly what QSerialPort does under load: write() returns a count, not
    an error, and the caller is responsible for the rest.
    """

    def __init__(self, chunk: int) -> None:
        self.chunk = chunk
        self.written = b""

    def write(self, view) -> int:
        # Match QIODevice.write's reliably supported Python input. PySide6
        # 6.11 lists memoryview in the signature but rejects it at runtime.
        assert isinstance(view, bytes)
        accepted = bytes(view)[: self.chunk]
        self.written += accepted
        return len(accepted)

    def waitForBytesWritten(self, _ms: int) -> bool:
        return True

    def flush(self) -> bool:
        return True

    def error(self):
        from PySide6.QtSerialPort import QSerialPort

        return QSerialPort.NoError

    def errorString(self) -> str:
        return ""


class DeadPort(ChunkedPort):
    """A port that has stopped accepting anything."""

    def write(self, _view) -> int:
        return 0

    def waitForBytesWritten(self, _ms: int) -> bool:
        return False

    def errorString(self) -> str:
        return "device disconnected"


COMMAND = (
    b'{"desk_buddy_command":"set_mqtt","server":"192.168.16.110","port":1883,'
    b'"tls":false,"user":"black","password":"password123","client_id":"black"}\n'
)


def test_a_short_write_still_sends_the_whole_command() -> None:
    """The regression: a truncated command reached the robot as garbage.

    QSerialPort.write() returning fewer bytes than asked is not an error, so
    a check for -1 passed it as success. The robot answered "Command must be
    valid JSON" while Studio said the settings were sent — and the 144-byte
    MQTT command hit this where the 77-byte Wi-Fi one did not.
    """
    for chunk in (1, 16, 100, 1000):
        view = monitor()
        view.serial = ChunkedPort(chunk)
        assert view._write_all(COMMAND) == "", f"chunk={chunk}"
        assert view.serial.written == COMMAND, f"chunk={chunk}"


def test_a_port_that_stops_accepting_is_reported_not_hung() -> None:
    view = monitor()
    view.serial = DeadPort(0)
    problem = view._write_all(COMMAND)
    assert problem
    assert "0 of" in problem
    assert "device disconnected" in problem


def test_a_write_error_reports_the_ports_own_message() -> None:
    class Failing(ChunkedPort):
        def write(self, _view) -> int:
            return -1

        def errorString(self) -> str:
            return "permission denied"

    view = monitor()
    view.serial = Failing(0)
    assert view._write_all(COMMAND) == "permission denied"


# ---- Provisioning a robot with an account it can actually use ------------
# The one field Studio can never fill in is the one the robot cannot connect
# without. The broker keeps a hash, so a managed account's password has to be
# typed here — and a robot sent a username with no password is refused with
# rc=5, minutes later, on its own serial log rather than in this dialog.


class FakeAccount:
    """An account as `system.accounts()` reports it.

    `password` is what Studio recorded when it created the account, and is
    empty for one created by hand with mosquitto_passwd.
    """

    def __init__(self, name: str, password: str = "") -> None:
        self.name = name
        self.password = password


def mqtt_dialog(on_save=None, accounts=None):
    from PySide6.QtWidgets import QWidget

    # The parent is returned too: a dialog whose parent is collected takes
    # its child widgets down with it mid-test.
    parent = QWidget()
    dialog = MqttDialog(
        parent,
        accounts=accounts if accounts is not None else [FakeAccount("black")],
        broker_host="192.168.16.110",
        broker_port=1883,
        on_save=on_save or (lambda *args: ""),
    )
    return dialog, parent


def test_a_recorded_password_is_filled_in() -> None:
    """The robot cannot connect without it, and the user should not have to
    go looking for a secret Studio generated for them."""
    dialog, _parent = mqtt_dialog(accounts=[FakeAccount("black", "s3cret")])
    dialog.account.setCurrentText("black")

    assert dialog.user.text() == "black"
    assert dialog.password.text() == "s3cret"
    # Still editable: the recorded value can be wrong if the password was
    # changed on the broker behind Studio's back.
    assert not dialog.password.isReadOnly()


def test_an_unrecorded_password_is_left_empty_and_explained() -> None:
    """An account made by hand has no record here, so it has to be typed —
    and the note has to say why, or the empty field looks like a bug."""
    dialog, _parent = mqtt_dialog(accounts=[FakeAccount("byhand")])
    dialog.account.setCurrentText("byhand")

    assert dialog.password.text() == ""
    assert not dialog.password.isReadOnly()
    assert "no password recorded" in dialog.broker_note.text().lower()


def test_saving_without_a_password_is_refused() -> None:
    """Better here than as rc=5 on the robot ten minutes later."""
    sent = []
    dialog, _parent = mqtt_dialog(on_save=lambda *args: sent.append(args) or "")
    dialog.account.setCurrentText("black")

    dialog.save()
    assert not sent, "nothing may be provisioned without a password"
    assert dialog.error.isVisibleTo(dialog)
    assert "password" in dialog.error.text().lower()

    # The broker's verdict is stubbed: this test is about the empty-field
    # guard, not about whether a given password happens to be right.
    from unittest import mock

    from . import serial_monitor

    dialog.password.setText("hunter2")
    with mock.patch.object(
        serial_monitor.broker_system, "password_works", lambda *a: True
    ):
        dialog.save()
    assert sent, "a typed password provisions normally"
    assert sent[0][3] == "hunter2"


def test_a_password_the_broker_refuses_never_reaches_the_robot() -> None:
    """A stale record is worse than an empty field.

    Studio records the password it generates, but `mosquitto_passwd` run by
    hand rewrites the hash without telling it. Provisioning that stale value
    looks like success and fails minutes later as rc=5 on the robot's own
    log, on a five-second retry loop, with nothing pointing back here.
    """
    from unittest import mock

    from . import serial_monitor

    sent = []
    dialog, _parent = mqtt_dialog(
        on_save=lambda *args: sent.append(args) or "",
        accounts=[FakeAccount("black", "stale")],
    )
    dialog.account.setCurrentText("black")

    with mock.patch.object(
        serial_monitor.broker_system, "password_works", lambda *a: False
    ):
        dialog.save()

    assert not sent, "a refused password must not be provisioned"
    assert "refuses this password" in dialog.error.text()
    # And it names the way out rather than just the problem.
    assert "mosquitto_passwd" in dialog.error.text()


def test_custom_is_not_checked_against_this_broker() -> None:
    """Custom points at some other broker entirely, so this one's opinion of
    the password is irrelevant — and asking it would reject valid cloud
    credentials."""
    from unittest import mock

    from . import serial_monitor

    asked = []
    dialog, _parent = mqtt_dialog()
    dialog.account.setCurrentText(CUSTOM_ACCOUNT)
    dialog.server.setText("mqtt.example.com")
    dialog.password.setText("cloud-secret")

    with mock.patch.object(
        serial_monitor.broker_system, "password_works",
        lambda *a: asked.append(a) or False,
    ):
        dialog.save()

    assert not asked, "Custom must not be checked against the local broker"


def test_custom_may_leave_the_password_blank() -> None:
    """Blank means "keep what the robot has" — a real thing to want when
    only the server address is changing."""
    sent = []
    dialog, _parent = mqtt_dialog(on_save=lambda *args: sent.append(args) or "")
    dialog.account.setCurrentText(CUSTOM_ACCOUNT)
    dialog.server.setText("mqtt.example.com")

    dialog.save()
    assert sent, "Custom is exempt from the password requirement"


def test_studio_is_not_offered_as_a_robot_account() -> None:
    """Studio's own credential reaches every topic and is already in use by
    this app. Giving it to a robot would hand that robot the whole bus, and
    two clients sharing one client ID get disconnected in turn by the
    broker."""
    from unittest import mock

    from . import serial_monitor

    offered = {}

    class FakeDialog:
        def __init__(self, _parent, *, accounts, **_kwargs):
            offered["names"] = [a.name for a in accounts]

        def exec(self):
            return 0

    monitor_view = monitor()
    monitor_view.serial.isOpen = lambda: True
    with mock.patch.object(
        serial_monitor.broker_system, "accounts",
        lambda: [FakeAccount(STUDIO_NAME, "x"), FakeAccount("black", "y")],
    ), mock.patch.object(serial_monitor, "MqttDialog", FakeDialog), \
            mock.patch.object(
                serial_monitor, "robot_endpoint", lambda: ("192.168.16.110", 1883)
            ):
        monitor_view.open_mqtt_dialog()

    assert offered["names"] == ["black"], offered
    assert STUDIO_NAME not in offered["names"]


def test_the_note_says_how_to_reset_a_forgotten_password() -> None:
    """"Studio cannot read it" is only half an answer without this."""
    dialog, _parent = mqtt_dialog()
    dialog.account.setCurrentText("black")
    assert "mosquitto_passwd" in dialog.broker_note.text()


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} serial monitor tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
