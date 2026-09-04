"""Broker account tests: records applied to a real Mosquitto.

    python -m studio.services.network.tests.account_tests

Runs a real broker on an unused port and creates real accounts, because the
thing worth proving is that a recorded credential actually connects — and that
one account cannot reach another's topics. The records themselves are tested
without a broker in `studio.models.tests`; this is only the half that needs
Mosquitto to be running to mean anything.

Every test uses a throwaway records file and its own broker directory, so a
run never touches the developer's real accounts and never leaves one behind.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ....models.config.mqtt_users import STUDIO_NAME, Users
from ....models.data.database import Database
from ....models.data.mqtt_messages import MqttMessages
from ....storage.store import Store
from .. import broker_commands as commands
from ..broker import accounts as svc
from ..broker.finder import find
from ..pub_sub.traffic import TrafficRecorder

TEST_PORT = 18892


def has_mosquitto() -> bool:
    return bool(find("mosquitto") and find("mosquitto_passwd"))


def can_connect(user: str, password: str) -> bool:
    """True when these credentials are accepted by the broker at all.

    Authentication failure is the one thing mosquitto_pub does report through
    its exit code, so this is a real check.
    """
    result = subprocess.run(
        ["mosquitto_pub", "-h", "127.0.0.1", "-p", str(TEST_PORT),
         "-u", user, "-P", password, "-t", f"{user}/ping", "-m", "x"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def publish(user: str, password: str, topic: str) -> bool:
    """True when this account is actually allowed to publish to this topic.

    Read from the broker's own log, not from mosquitto_pub's exit code. A
    topic denied by the ACL is dropped silently and still acknowledged — the
    broker logs "Denied PUBLISH" and the client exits 0 regardless — so an
    exit-code check would call every publish a success and prove nothing.
    """
    marker = f"probe-{secrets.token_hex(4)}"
    before = _log_size()
    subprocess.run(
        ["mosquitto_pub", "-q", "1", "-h", "127.0.0.1", "-p", str(TEST_PORT),
         "-u", user, "-P", password, "-t", topic, "-m", marker],
        capture_output=True, text=True,
    )
    time.sleep(0.2)
    return "Denied PUBLISH" not in _log_tail(before)


# Set by Sandbox while a broker is running, so the publish helper knows which
# log to read. The broker Studio ships does not log to a file — these tests
# start their own with logging on rather than changing what users get.
_log_path: Path | None = None


def _log_size() -> int:
    if _log_path is None or not _log_path.exists():
        return 0
    return _log_path.stat().st_size


def _log_tail(offset: int) -> str:
    if _log_path is None or not _log_path.exists():
        return ""
    with _log_path.open() as handle:
        handle.seek(offset)
        return handle.read()


class Sandbox:
    """A throwaway records file and broker directory, for one test.

    Both are patched rather than configured: the point is that a test can
    never write to the real ones, and a missed cleanup cannot leak into the
    developer's own accounts.

    The broker is started by the test rather than by commands.start(), with
    logging turned on — an ACL denial is only observable in the broker's log
    (see `publish`), and the shipped config has no reason to write one.
    """

    def __init__(self) -> None:
        self._directory = Path(tempfile.mkdtemp())
        self.users = Users(Store("mqtt_users", directory=self._directory))
        self.log = self._directory / "broker.log"
        self._process: subprocess.Popen | None = None
        self._patches = [
            mock.patch.object(commands, "broker_dir", lambda: self._directory),
            mock.patch(
                "studio.services.network.broker.accounts.users",
                lambda: self.users,
            ),
        ]

    def __enter__(self) -> "Sandbox":
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop_broker()
        for patch in self._patches:
            patch.stop()

    def start_broker(self) -> None:
        """Start a logging broker, and point the service's reload at it."""
        global _log_path
        commands.ensure_account_files()
        config = self._directory / "mosquitto.conf"
        config.write_text(
            f"listener {TEST_PORT} 127.0.0.1\n"
            "allow_anonymous false\n"
            f"password_file {commands.passwd_path()}\n"
            f"acl_file {commands.acl_path()}\n"
            f"log_dest file {self.log}\n"
            "log_type all\n"
        )
        config.chmod(0o600)

        self._process = subprocess.Popen(
            [find("mosquitto"), "-c", str(config)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        # reload_broker() signals whatever commands thinks it started, so the
        # module-level handle has to be this process for a sync to land.
        commands._process = self._process
        _log_path = self.log
        _wait_for_port()

    def stop_broker(self) -> None:
        global _log_path
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
        self._process = None
        commands._process = None
        _log_path = None


def _wait_for_port(timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if commands.port_open(TEST_PORT):
            return
        time.sleep(0.05)


def _wait_until(check, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


# ---- What a recorded account can actually do -----------------------------

def test_a_recorded_account_can_connect() -> None:
    if not has_mosquitto():
        return
    with Sandbox() as box:
        created, problem = box.users.add("black")
        assert created, problem

        box.start_broker()
        try:
            assert svc.sync() == ""
            assert can_connect("black", created.password)
            assert not can_connect("black", "wrong-password")
            assert publish("black", created.password, "black/test")
        finally:
            box.stop_broker()


def test_accounts_are_isolated_to_their_own_topics() -> None:
    """The whole point of per-account ACLs: two robots cannot hear each other."""
    if not has_mosquitto():
        return
    with Sandbox() as box:
        black, _ = box.users.add("black")
        silver, _ = box.users.add("silver")

        box.start_broker()
        try:
            assert svc.sync() == ""
            assert publish("black", black.password, "black/telemetry")
            assert not publish("black", black.password, "silver/telemetry")
            assert publish("silver", silver.password, "silver/telemetry")
        finally:
            box.stop_broker()


def test_full_access_account_sees_everything() -> None:
    if not has_mosquitto():
        return
    with Sandbox() as box:
        vision, _ = box.users.add("vision", full_access=True)

        box.start_broker()
        try:
            assert svc.sync() == ""
            assert publish("vision", vision.password, "anything/at/all")
            assert publish("vision", vision.password, "black/telemetry")
        finally:
            box.stop_broker()


def test_studio_account_survives_a_restart() -> None:
    """The bug this whole records layer exists to fix.

    Studio's password used to live only in Mosquitto's hash, which cannot be
    read back, so every launch minted a new one. Now the record keeps it: a
    second `ensure_studio()` — the same call a fresh launch makes — returns
    the account that is already there, and it still connects.
    """
    if not has_mosquitto():
        return
    with Sandbox() as box:
        first = box.users.ensure_studio()
        again = box.users.ensure_studio()
        assert again.password == first.password, "a new password was minted"

        # A genuinely fresh reader of the same file, as a new launch would be.
        reloaded = Users(Store("mqtt_users", directory=box._directory))
        assert reloaded.find(STUDIO_NAME).password == first.password

        box.start_broker()
        try:
            assert svc.sync() == ""
            assert can_connect(STUDIO_NAME, first.password)
            assert publish(STUDIO_NAME, first.password, "anything/at/all")
        finally:
            box.stop_broker()


def test_sync_drops_an_account_that_was_removed() -> None:
    """A removed record has to stop working, not linger in the passwd file."""
    if not has_mosquitto():
        return
    with Sandbox() as box:
        created, _ = box.users.add("black")

        box.start_broker()
        try:
            assert svc.sync() == ""
            assert can_connect("black", created.password)

            assert box.users.remove("black") == ""
            assert svc.sync() == ""
            assert not can_connect("black", created.password)
        finally:
            box.stop_broker()


def test_topic_edits_reach_the_broker() -> None:
    if not has_mosquitto():
        return
    with Sandbox() as box:
        created, _ = box.users.add("black")

        box.start_broker()
        try:
            assert svc.sync() == ""
            assert not publish("black", created.password, "shared/news")

            box.users.add_topic("black", "shared/news")
            assert svc.sync() == ""
            assert publish("black", created.password, "shared/news")

            box.users.remove_topic("black", "shared/news")
            assert svc.sync() == ""
            assert not publish("black", created.password, "shared/news")
        finally:
            box.stop_broker()


def test_reload_does_not_restart_the_broker() -> None:
    """An account added mid-session must not drop existing connections."""
    if not has_mosquitto():
        return
    with Sandbox() as box:
        box.users.add("black")

        box.start_broker()
        try:
            svc.sync()
            before = commands._process.pid
            box.users.add("silver")
            svc.sync()
            assert commands._process.pid == before, "the broker was restarted"
        finally:
            box.stop_broker()


def test_traffic_recorder_persists_a_real_publication() -> None:
    if not has_mosquitto():
        return
    with Sandbox() as box:
        studio = box.users.ensure_studio()
        box.start_broker()
        recorder = TrafficRecorder(
            MqttMessages(Database(box._directory / "traffic.sqlite3"))
        )
        try:
            assert svc.sync() == ""
            with mock.patch(
                "studio.services.network.pub_sub.traffic.users",
                lambda: box.users,
            ):
                recorder.start(TEST_PORT)
                assert _wait_until(
                    lambda: recorder.status.startswith("Recording all")
                ), recorder.status
                result = subprocess.run(
                    [
                        "mosquitto_pub", "-h", "127.0.0.1", "-p", str(TEST_PORT),
                        "-u", studio.name, "-P", studio.password,
                        "-t", "black/telemetry", "-m", '{"angle":42}',
                    ],
                    capture_output=True,
                    text=True,
                )
                assert result.returncode == 0, result.stderr
                assert _wait_until(lambda: recorder.store.count() == 1)
                saved = recorder.store.recent(include_heartbeats=True)[0]
                assert saved.topic == "black/telemetry"
                assert saved.payload_text == '{"angle":42}'
        finally:
            recorder.stop()
            box.stop_broker()


def main() -> int:
    if not has_mosquitto():
        print("SKIP: mosquitto is not installed")
        return 0

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} account tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
