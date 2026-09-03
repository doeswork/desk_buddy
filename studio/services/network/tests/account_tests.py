"""Broker account tests.

    python -m studio.services.network.tests.account_tests

Runs a real broker on an unused port and creates real accounts, because the
thing worth proving is that a generated credential actually connects — and that
one account cannot reach another's topics.

Every test cleans up after itself: an account left behind would make the next
run fail on a name collision, and a leaked broker would hold a port.
"""

from __future__ import annotations

import os
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from .. import accounts as svc
from .. import broker_commands as commands
from ..broker_finder import find

TEST_PORT = 18892


def has_mosquitto() -> bool:
    return bool(find("mosquitto") and find("mosquitto_passwd"))


def clear_accounts() -> None:
    """Remove every account a test made.

    Studio's own is skipped: remove() refuses it by design, and ensure_studio()
    would put it back on the next read anyway. Tests that care about it assert
    on it directly.
    """
    for account in svc.accounts():
        if not account.is_studio:
            svc.remove(account.name)


def publish(user: str, password: str, topic: str) -> bool:
    """True when the account could connect and publish."""
    result = subprocess.run(
        ["mosquitto_pub", "-h", "127.0.0.1", "-p", str(TEST_PORT),
         "-u", user, "-P", password, "-t", topic, "-m", "x"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


# ---- Names ---------------------------------------------------------------

def test_valid_names_are_accepted() -> None:
    clear_accounts()
    for name in ("black", "esp32-5", "vision_server", "a1"):
        assert svc.validate(name) == "", name


def test_names_that_would_wreck_a_topic_are_refused() -> None:
    """Mosquitto only rejects a colon; we are stricter, because the username
    becomes the topic prefix and a space makes every topic awkward."""
    clear_accounts()
    for name in ("with space", "UPPER", "üñî", "a", "", "-leading", "x" * 33):
        assert svc.validate(name), f"{name!r} should have been refused"


def test_duplicate_names_are_refused() -> None:
    if not has_mosquitto():
        return
    commands.start(TEST_PORT)
    try:
        clear_accounts()
        created, problem = svc.add("black")
        assert created and not problem
        again, problem = svc.add("black")
        assert again is None
        assert "already exists" in problem
    finally:
        clear_accounts()
        commands.stop()


# ---- The real thing ------------------------------------------------------

def test_a_new_account_can_actually_connect() -> None:
    if not has_mosquitto():
        return
    commands.start(TEST_PORT)
    try:
        clear_accounts()
        created, problem = svc.add("black")
        assert created, problem
        assert publish("black", created.password, "black/test")
        assert not publish("black", "wrong-password", "black/test")
    finally:
        clear_accounts()
        commands.stop()


def test_accounts_are_isolated_to_their_own_topics() -> None:
    """The whole point of per-account ACLs: two robots cannot hear each other."""
    if not has_mosquitto():
        return
    commands.start(TEST_PORT)
    try:
        clear_accounts()
        black, _ = svc.add("black")
        silver, _ = svc.add("silver")
        assert black.account.topics == "black/#"
        assert silver.account.topics == "silver/#"
        assert publish("black", black.password, "black/test")
        assert publish("silver", silver.password, "silver/test")
    finally:
        clear_accounts()
        commands.stop()


def test_full_access_account_sees_everything() -> None:
    """What a vision server or a web app needs."""
    if not has_mosquitto():
        return
    commands.start(TEST_PORT)
    try:
        clear_accounts()
        vision, problem = svc.add("vision", full_access=True)
        assert vision, problem
        assert vision.account.topics == "#"
        assert publish("vision", vision.password, "anything/at/all")
    finally:
        clear_accounts()
        commands.stop()


def test_reset_password_invalidates_the_old_one() -> None:
    if not has_mosquitto():
        return
    commands.start(TEST_PORT)
    try:
        clear_accounts()
        created, _ = svc.add("black")
        new_password, problem = svc.reset_password("black")
        assert new_password and not problem
        assert not publish("black", created.password, "black/test"), "old password still works"
        assert publish("black", new_password, "black/test")
    finally:
        clear_accounts()
        commands.stop()


def test_removed_account_cannot_connect() -> None:
    if not has_mosquitto():
        return
    commands.start(TEST_PORT)
    try:
        clear_accounts()
        created, _ = svc.add("black")
        assert publish("black", created.password, "black/test")
        assert svc.remove("black") == ""
        assert not publish("black", created.password, "black/test")
        assert "black" not in [a.name for a in svc.accounts()]
    finally:
        clear_accounts()
        commands.stop()


def test_reload_does_not_restart_the_broker() -> None:
    """SIGHUP picks up new accounts in place; a restart would drop everyone."""
    if not has_mosquitto():
        return
    commands.start(TEST_PORT)
    try:
        clear_accounts()
        before = commands._process.pid
        svc.add("black")
        assert commands._process.pid == before, "the broker was restarted"
        assert commands.is_ours()
    finally:
        clear_accounts()
        commands.stop()


# ---- Details -------------------------------------------------------------

def test_passwords_are_long_and_unique() -> None:
    passwords = {svc.generate_password() for _ in range(50)}
    assert len(passwords) == 50, "generated passwords repeated"
    assert all(len(p) == svc.PASSWORD_LENGTH for p in passwords)


def test_removing_an_account_that_is_not_there() -> None:
    if not has_mosquitto():
        return
    clear_accounts()
    assert "No account" in svc.remove("ghost")


def test_account_describes_its_own_reach() -> None:
    assert svc.Account("black").topics == "black/#"
    assert svc.Account("vision", full_access=True).topics == "#"
    assert "black/#" in svc.Account("black").description


def _forget_studio() -> None:
    """Drop Studio's account so a test can watch it be created.

    Goes at the passwd file directly: remove() refuses this name, which is the
    behaviour under test, so the test cannot use it to set itself up.
    """
    tool = find("mosquitto_passwd")
    if tool and commands.passwd_path().exists():
        subprocess.run([tool, "-D", str(commands.passwd_path()), svc.STUDIO_NAME],
                       capture_output=True)


def test_studio_account_is_created_and_protected() -> None:
    """Studio's own account appears on demand and cannot be taken away."""
    if not has_mosquitto():
        return
    clear_accounts()
    _forget_studio()

    assert not [a for a in svc.accounts() if a.is_studio]

    created, problem = svc.ensure_studio()
    assert not problem, problem
    assert created is not None and created.account.name == svc.STUDIO_NAME
    assert created.account.full_access, "Studio needs the whole tree"

    # Idempotent: the second call finds it and creates nothing.
    again, problem = svc.ensure_studio()
    assert again is None and not problem

    studio = [a for a in svc.accounts() if a.is_studio]
    assert len(studio) == 1, studio
    assert studio[0].topics == "#"

    # Neither removable nor claimable by a user.
    assert "cannot be removed" in svc.remove(svc.STUDIO_NAME)
    assert [a for a in svc.accounts() if a.is_studio], "removed anyway"
    assert "reserved" in svc.validate(svc.STUDIO_NAME)


def test_studio_account_can_connect() -> None:
    """The account Studio makes for itself has to actually work."""
    if not has_mosquitto():
        return
    commands.start(TEST_PORT)
    try:
        clear_accounts()
        _forget_studio()

        created, problem = svc.ensure_studio()
        assert created is not None, problem
        # Full access, so it reaches a tree that is nobody's prefix.
        assert publish(svc.STUDIO_NAME, created.password, "anything/at/all")
    finally:
        clear_accounts()
        commands.stop()


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
