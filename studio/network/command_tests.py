"""Broker start / stop / restart tests.

    python -m studio.network.command_tests

These run a real Mosquitto on an unused high port. They are skipped when it is
not installed, so the suite still passes on a machine without it — and they
never touch a broker on 1883, because stopping a broker the developer set up
for something else would be exactly the bug this module exists to avoid.
"""

from __future__ import annotations

import os
import socket

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from . import broker_commands as commands
from .broker_finder import find, port_open

# Well outside both Mosquitto's default and Studio's own.
TEST_PORT = 18897


def has_mosquitto() -> bool:
    return bool(find("mosquitto"))


def cleanup() -> None:
    if commands.is_ours():
        commands.stop()


def test_start_then_stop() -> None:
    try:
        started = commands.start(TEST_PORT)
        assert started.ok, started.message
        assert port_open(TEST_PORT), "start() returned ok but nothing is listening"
        assert commands.is_ours()

        stopped = commands.stop()
        assert stopped.ok, stopped.message
        assert not port_open(TEST_PORT), "stop() returned ok but it is still up"
        assert not commands.is_ours()
    finally:
        cleanup()


def test_start_is_idempotent() -> None:
    try:
        commands.start(TEST_PORT)
        again = commands.start(TEST_PORT)
        assert again.ok
        assert "already running" in again.message
    finally:
        cleanup()


def test_restart_leaves_it_running() -> None:
    try:
        commands.start(TEST_PORT)
        result = commands.restart(TEST_PORT)
        assert result.ok, result.message
        assert port_open(TEST_PORT)
    finally:
        cleanup()


def test_restart_from_stopped_just_starts() -> None:
    try:
        assert not commands.is_ours()
        result = commands.restart(TEST_PORT)
        assert result.ok, result.message
        assert port_open(TEST_PORT)
    finally:
        cleanup()


def test_start_reports_a_busy_port() -> None:
    """And says so in words, rather than hanging or raising."""
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", TEST_PORT))
    blocker.listen(1)
    try:
        result = commands.start(TEST_PORT)
        assert not result.ok
        assert "already in use" in result.message
        assert result.detail, "a failure the user must act on needs a detail"
    finally:
        blocker.close()
        cleanup()


def test_stop_refuses_a_broker_we_did_not_start() -> None:
    """The important one: a system broker must never be stopped from here."""
    assert not commands.is_ours()
    result = commands.stop()
    assert not result.ok
    assert "did not start" in result.message


def test_result_is_falsey_when_it_failed() -> None:
    assert not commands.CommandResult(False, "nope")
    assert commands.CommandResult(True, "fine")


def test_config_is_written_with_our_port() -> None:
    path = commands.write_config(TEST_PORT)
    text = path.read_text()
    assert f"listener {TEST_PORT} 127.0.0.1" in text
    assert "allow_anonymous false" in text, "an open broker would be a security bug"
    assert str(commands.passwd_path()) in text


def test_broker_files_are_private() -> None:
    """mosquitto_passwd refuses world-readable files in newer versions."""
    commands.write_config(TEST_PORT)
    assert oct(commands.broker_dir().stat().st_mode)[-3:] == "700"
    assert oct(commands.passwd_path().stat().st_mode)[-3:] == "600"


def test_broker_dir_is_under_the_app_data_location() -> None:
    """Not the bare ~/.local/share, and not named after the running script."""
    path = str(commands.broker_dir())
    assert path.endswith("DeskBuddy/Studio/broker"), path


def main() -> int:
    if not has_mosquitto():
        print("SKIP: mosquitto is not installed")
        return 0

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} broker command tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
