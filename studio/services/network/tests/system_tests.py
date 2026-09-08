"""The system-broker mode: what Studio reports, and what it refuses to do.

Every test here uses a throwaway settings backend. The mode and the recorded
credentials are real persisted settings, so a test that used the default one
would rewrite the developer's own broker configuration.

    QT_QPA_PLATFORM=offscreen python -m studio.services.network.tests.system_tests
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from ....storage import keys
from ....storage.settings import Settings
from ..broker import system

_app = QApplication.instance() or QApplication([])


def sandbox() -> Settings:
    """A Settings backed by its own throwaway file."""
    path = Path(tempfile.mkdtemp()) / "settings.ini"
    return Settings(QSettings(str(path), QSettings.IniFormat))


# ---- Where the broker is -------------------------------------------------

def test_an_empty_host_means_this_machine() -> None:
    """Blank is resolved to the LAN address, never left as loopback.

    The address is handed to robots, and a robot cannot reach 127.0.0.1 on
    someone else's machine.
    """
    store = sandbox()
    with mock.patch.object(system, "lan_address", lambda: "192.168.1.50"):
        assert system.host(store) == "192.168.1.50"


def test_a_recorded_host_is_used_as_given() -> None:
    store = sandbox()
    system.set_connection(
        host_value="10.0.0.9", port_value=1884, user="u", password="p",
        backend=store,
    )
    assert system.host(store) == "10.0.0.9"
    assert system.port(store) == 1884
    assert system.credentials(store) == ("u", "p")


def test_port_defaults_to_1883() -> None:
    """The port every MQTT client already looks on."""
    assert system.port(sandbox()) == 1883


# ---- Configured means proven, not merely recorded ------------------------

def test_recorded_credentials_are_not_yet_configured() -> None:
    """Writing a password down says nothing about the account existing.

    /etc/mosquitto/passwd is root-owned and holds hashes, so the only proof
    is a successful connection. Until then the Broker page must keep
    offering the setup step rather than claiming to be ready.
    """
    broker = system.SystemBroker(
        host="192.168.1.50", port=1883, reachable=True,
        user="studio", has_password=True,
        installed=True, service_active=True, verified=False,
    )
    assert broker.recorded
    assert not broker.configured
    assert not broker.usable


def test_verified_credentials_are_configured() -> None:
    broker = system.SystemBroker(
        host="192.168.1.50", port=1883, reachable=True,
        user="studio", has_password=True,
        installed=True, service_active=True, verified=True,
    )
    assert broker.configured
    assert broker.usable


def test_a_verified_account_is_still_unusable_when_the_broker_is_down() -> None:
    broker = system.SystemBroker(
        host="192.168.1.50", port=1883, reachable=False,
        user="studio", has_password=True,
        installed=True, service_active=False, verified=True,
    )
    assert broker.configured
    assert not broker.usable


def test_suggested_account_keeps_its_password() -> None:
    """The command shown must use the password Studio will actually send.

    Regenerating per call would print one password and authenticate with
    another — the failure would look like the user mistyping it.
    """
    store = sandbox()
    first = system.suggested_account(store)
    assert first == system.suggested_account(store)
    assert first[0] == system.SUGGESTED_USER
    assert len(first[1]) >= 16


# ---- What the user is told to run ----------------------------------------

def test_install_instructions_match_the_package_manager() -> None:
    """A pacman command on an apt machine is worse than no command."""
    with mock.patch.object(system.platform, "system", lambda: "Linux"):
        with mock.patch.object(
            system.shutil, "which", lambda tool: tool == "apt"
        ):
            steps = system.install_instructions()
            assert "apt install" in steps[0][1]

        with mock.patch.object(
            system.shutil, "which", lambda tool: tool == "pacman"
        ):
            steps = system.install_instructions()
            assert "pacman -S" in steps[0][1]


def test_listener_instructions_expose_the_broker_to_the_lan() -> None:
    """A default Mosquitto listens on loopback, which no robot can reach."""
    steps = system.listener_instructions(1883)
    joined = " ".join(command for _caption, command in steps)
    assert "0.0.0.0" in joined
    assert "1883" in joined


# ---- Wording -------------------------------------------------------------

def test_the_headline_says_only_whether_the_broker_is_running() -> None:
    """One question per line.

    The headline used to fold the connection into itself ("account
    needed", "Using the system broker"), which made a running broker and a
    working login the same sentence — and left no plain place to say the
    one thing a user asks first.
    """
    def broker(**kwargs):
        base = dict(
            host="192.168.1.50", port=1883, reachable=True,
            user="studio", has_password=True,
            installed=True, service_active=True, verified=True,
        )
        base.update(kwargs)
        return system.SystemBroker(**base)

    assert "not installed" in broker(installed=False).headline
    assert broker(reachable=False).headline == "Broker not running"
    # Running is running, whether or not Studio has an account on it yet.
    assert broker(verified=False).headline == "Broker running on 192.168.1.50:1883"
    assert broker().headline == "Broker running on 192.168.1.50:1883"


def test_the_detail_says_whether_studio_is_connected() -> None:
    """The second fact, kept separate from the first.

    A running broker that refuses Studio's account looks identical to a
    working one unless this line distinguishes them.
    """
    def broker(**kwargs):
        base = dict(
            host="192.168.1.50", port=1883, reachable=True,
            user="studio", has_password=True,
            installed=True, service_active=True, verified=True,
        )
        base.update(kwargs)
        return system.SystemBroker(**base)

    assert "not connected" in broker(verified=False).detail
    assert "Connected as" in broker().detail
    assert "studio" in broker().detail


def test_describe_connects_rather_than_waiting_to_be_asked() -> None:
    """The setup finishes itself.

    Studio used to authenticate only when a button was pressed, so a user
    who had run every command still saw "account needed" until they found
    that button — the connection was the one fact the page could not tell
    them without being told to look.
    """
    store = sandbox()
    system.set_connection(
        host_value="192.168.1.50", user="studio", password="secret",
        backend=store,
    )
    tried: list[bool] = []

    def fake_verify(backend=None):
        tried.append(True)
        backend.set(keys.SYSTEM_BROKER_VERIFIED, True)
        return ""

    with mock.patch.object(system, "port_open", lambda *a, **k: True), \
            mock.patch.object(system, "verify", fake_verify):
        state = system.describe(store)

    assert tried, "a reachable broker with credentials is tried"
    assert state.configured


def test_describe_does_not_reconnect_once_it_has_worked() -> None:
    """A verified account is not re-tested on every page build."""
    store = sandbox()
    system.set_connection(
        host_value="192.168.1.50", user="studio", password="secret",
        backend=store,
    )
    store.set(keys.SYSTEM_BROKER_VERIFIED, True)
    tried: list[bool] = []

    with mock.patch.object(system, "port_open", lambda *a, **k: True), \
            mock.patch.object(
                system, "verify", lambda backend=None: tried.append(True) or ""
            ):
        assert system.describe(store).configured

    assert not tried, "already verified: nothing to prove"


def test_describe_does_not_connect_with_nothing_to_try() -> None:
    """No credentials, or no broker answering — nothing to authenticate."""
    tried: list[bool] = []
    fake = lambda backend=None: tried.append(True) or ""

    # Reachable, but no account recorded yet.
    store = sandbox()
    with mock.patch.object(system, "port_open", lambda *a, **k: True), \
            mock.patch.object(system, "verify", fake):
        system.describe(store)
    assert not tried

    # Credentials, but the broker is down.
    store = sandbox()
    system.set_connection(
        host_value="192.168.1.50", user="u", password="p", backend=store
    )
    with mock.patch.object(system, "port_open", lambda *a, **k: False), \
            mock.patch.object(system, "verify", fake):
        system.describe(store)
    assert not tried


def test_describe_can_skip_the_connection() -> None:
    """For callers that only want the cheap facts, with no round trip."""
    store = sandbox()
    system.set_connection(
        host_value="192.168.1.50", user="u", password="p", backend=store
    )
    tried: list[bool] = []

    with mock.patch.object(system, "port_open", lambda *a, **k: True), \
            mock.patch.object(
                system, "verify", lambda backend=None: tried.append(True) or ""
            ):
        system.describe(store, connect=False)

    assert not tried


def test_an_unreachable_broker_says_whether_the_service_is_up() -> None:
    """"Installed but not started" and "started but not listening" differ."""
    stopped = system.SystemBroker(
        host="h", port=1883, reachable=False, user="", has_password=False,
        installed=True, service_active=False,
    )
    assert "service is not" in stopped.detail

    running = system.SystemBroker(
        host="h", port=1883, reachable=False, user="", has_password=False,
        installed=True, service_active=True,
    )
    assert "nothing answered" in running.detail


# ---- What a robot is told -----------------------------------------------

def broker_state(**kwargs) -> system.SystemBroker:
    base = dict(
        host="192.168.1.50", port=1883, reachable=True,
        user="studio", has_password=True,
        installed=True, service_active=True, verified=True,
    )
    base.update(kwargs)
    return system.SystemBroker(**base)


def test_robot_endpoint_is_the_machines_broker() -> None:
    """The robot is pointed at the broker this machine runs.

    An earlier version refused a broker on 1883 outright, on the grounds
    that Studio did not manage its accounts. Using that broker is now the
    only thing Studio does.
    """
    from ..broker import finder

    with (
        mock.patch("studio.storage.settings.settings", return_value=sandbox()),
        mock.patch.object(system, "describe", lambda *a, **k: broker_state()),
        mock.patch.object(finder, "is_wsl", return_value=False),
    ):
        assert finder.robot_endpoint() == ("192.168.1.50", 1883)


def test_robot_endpoint_is_empty_when_the_broker_is_down() -> None:
    """Nothing usable to hand over is said plainly, not guessed at."""
    from ..broker import finder

    down = broker_state(reachable=False, service_active=False)
    with (
        mock.patch("studio.storage.settings.settings", return_value=sandbox()),
        mock.patch.object(system, "describe", lambda *a, **k: down),
        mock.patch.object(finder, "is_wsl", return_value=False),
    ):
        assert finder.robot_endpoint() == ("", 0)


def test_a_loopback_broker_is_never_given_to_a_robot() -> None:
    """127.0.0.1 would point the robot back at itself."""
    from ..broker import finder

    local = broker_state(host="127.0.0.1")
    with (
        mock.patch("studio.storage.settings.settings", return_value=sandbox()),
        mock.patch.object(system, "describe", lambda *a, **k: local),
        mock.patch.object(finder, "is_wsl", return_value=False),
    ):
        with mock.patch.object(finder, "lan_address", lambda: "192.168.1.50"):
            assert finder.robot_endpoint() == ("192.168.1.50", 1883)


# ---- May Studio change the broker's accounts? ---------------------------

def access(**kwargs) -> system.WriteAccess:
    base = dict(
        passwd_writable=True, acl_writable=True, dir_writable=True,
        tool="/usr/bin/mosquitto_passwd", files_exist=True,
    )
    base.update(kwargs)
    return system.WriteAccess(**base)


def test_write_access_needs_every_part() -> None:
    """Any one missing piece means Studio must not offer to write.

    The directory matters as much as the files: mosquitto_passwd writes a
    backup alongside the file it edits, so a writable passwd inside a
    read-only directory fails with a confusing error about a backup rather
    than about permissions. That is exactly what happened in practice.
    """
    assert access().allowed
    assert not access(passwd_writable=False).allowed
    assert not access(acl_writable=False).allowed
    assert not access(dir_writable=False).allowed
    assert not access(tool="").allowed
    assert not access(files_exist=False).allowed


def test_write_access_says_why_not() -> None:
    """The page shows this verbatim, so it has to name the actual blocker."""
    assert access().reason == ""
    assert "mosquitto_passwd" in access(tool="").reason
    assert "passwd" in access(passwd_writable=False).reason
    assert "acl" in access(acl_writable=False).reason
    assert "backup" in access(dir_writable=False).reason


def test_writing_is_refused_without_access() -> None:
    """Second line of defence: the button is disabled, but never trust that."""
    with mock.patch.object(
        system, "write_access", lambda: access(dir_writable=False)
    ):
        change = system.create_account("someone", "password123")
        assert not change.changed
        assert "backup" in change.problem

        change = system.remove_account("someone")
        assert not change.changed
        assert change.problem


def test_direct_account_reload_reports_written_and_applied_separately() -> None:
    store = sandbox()
    environment = SimpleNamespace(wsl=False, service_manager="systemd")
    with mock.patch.object(system, "settings", return_value=store), \
            mock.patch(
                "studio.services.network.broker.setup_platform.detect",
                return_value=environment,
            ), \
            mock.patch(
                "studio.services.network.broker.setup_platform.service_commands",
                return_value=[["systemctl", "reload", "mosquitto"]],
            ), \
            mock.patch.object(
                system.subprocess, "run",
                return_value=mock.Mock(returncode=0, stdout="", stderr=""),
            ):
        change = system.reload()

    assert change == system.AccountChange(changed=True, applied=True)
    assert not store.get(keys.SYSTEM_BROKER_RELOAD_PENDING)


def test_refused_account_reload_preserves_the_pending_change() -> None:
    store = sandbox()
    environment = SimpleNamespace(wsl=False, service_manager="systemd")
    with mock.patch.object(system, "settings", return_value=store), \
            mock.patch(
                "studio.services.network.broker.setup_platform.detect",
                return_value=environment,
            ), \
            mock.patch(
                "studio.services.network.broker.setup_platform.service_commands",
                return_value=[["systemctl", "reload", "mosquitto"]],
            ), \
            mock.patch.object(
                system.subprocess, "run",
                return_value=mock.Mock(
                    returncode=1,
                    stdout="",
                    stderr="Interactive authentication required.",
                ),
            ):
        change = system.reload()

    assert change.changed and not change.applied
    assert "refused" in change.problem
    assert store.get(keys.SYSTEM_BROKER_RELOAD_PENDING)


def test_wsl_account_reload_goes_straight_to_the_privileged_helper() -> None:
    store = sandbox()
    environment = SimpleNamespace(wsl=True, service_manager="systemd")
    with mock.patch.object(system, "settings", return_value=store), \
            mock.patch(
                "studio.services.network.broker.setup_platform.detect",
                return_value=environment,
            ), \
            mock.patch.object(system.subprocess, "run") as run:
        change = system.reload()

    assert change == system.AccountChange(changed=True)
    assert store.get(keys.SYSTEM_BROKER_RELOAD_PENDING)
    run.assert_not_called()


def test_created_account_and_password_survive_a_pending_reload() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        passwd = root / "passwd"
        acl = root / "acl"
        passwd.write_text("")
        acl.write_text("")
        with mock.patch.object(system, "PASSWD_FILE", passwd), \
                mock.patch.object(system, "ACL_FILE", acl), \
                mock.patch.object(system, "write_access", return_value=access()), \
                mock.patch.object(
                    system.subprocess, "run",
                    return_value=mock.Mock(returncode=0, stdout="", stderr=""),
                ), \
                mock.patch.object(system, "_record_password") as record, \
                mock.patch.object(
                    system, "reload",
                    return_value=system.AccountChange(changed=True),
                ):
            change = system.create_account(
                "ubuntu-test", "generated-password", "ubuntu-test/#"
            )
            acl_text = acl.read_text()

    assert change.changed and not change.applied
    record.assert_called_once_with("ubuntu-test", "generated-password")
    assert "user ubuntu-test" in acl_text


def test_create_edit_and_remove_share_the_reload_result() -> None:
    pending = system.AccountChange(changed=True)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        passwd = root / "passwd"
        acl = root / "acl"
        passwd.write_text("robot:hash\n")
        acl.write_text("user robot\ntopic readwrite robot/#\n")
        with mock.patch.object(system, "PASSWD_FILE", passwd), \
                mock.patch.object(system, "ACL_FILE", acl), \
                mock.patch.object(system, "write_access", return_value=access()), \
                mock.patch.object(
                    system.subprocess, "run",
                    return_value=mock.Mock(returncode=0, stdout="", stderr=""),
                ), \
                mock.patch.object(system, "_record_password"), \
                mock.patch.object(system, "_forget_password"), \
                mock.patch.object(system, "reload", return_value=pending) as reload:
            assert system.create_account("new-robot", "password", "new-robot/#") == pending
            assert system.set_topics("robot", ("robot/#", "shared/#")) == pending
            assert system.remove_account("robot") == pending
    assert reload.call_count == 3


def test_grant_instructions_are_the_copyable_fallback() -> None:
    """The printed steps stay `sudo` commands the user runs in a terminal.

    These are the fallback path — for a machine with no polkit agent, and
    for anyone who would rather read the change before making it. Studio
    never runs these itself; `grant_access()` is the in-app route, and it
    goes through pkexec instead.
    """
    commands = [command for _caption, command in system.grant_instructions()]
    assert commands, "there must be a way to grant access"
    assert all(command.startswith("sudo ") for command in commands)


def test_granting_never_puts_a_password_through_studio() -> None:
    """The invariant that matters: Studio does not collect or carry secrets.

    It may *ask the system* to elevate — that is what pkexec is for, and the
    desktop's own agent does the asking in its own window. What it must
    never do is prompt for a password itself, or pass one to a subprocess.
    """
    seen: dict = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(system, "can_grant", lambda: True), \
            mock.patch.object(system, "find", lambda name: f"/usr/bin/{name}"), \
            mock.patch.object(system, "write_access", lambda: access()), \
            mock.patch.object(system.subprocess, "run", fake_run):
        assert system.grant_access() == ""

    command = seen["command"]
    assert command[0] == "/usr/bin/pkexec", "elevation goes through polkit"
    # No password anywhere: not on the command line, not on stdin.
    assert "input" not in seen["kwargs"]
    assert not any("passwd -b" in part for part in command)
    # And no nested sudo — one elevation mechanism, not two.
    assert not any(part.startswith("sudo") for part in command)


def test_grant_reports_a_dismissed_prompt_as_a_choice() -> None:
    """126 is pkexec for "cancelled". Not an error — an answer."""
    with mock.patch.object(system, "can_grant", lambda: True), \
            mock.patch.object(system, "find", lambda name: f"/usr/bin/{name}"), \
            mock.patch.object(
                system.subprocess, "run",
                lambda *a, **k: mock.Mock(returncode=126, stdout="", stderr=""),
            ):
        problem = system.grant_access()
    assert "Cancelled" in problem


def test_grant_believes_the_filesystem_over_the_exit_code() -> None:
    """What settles success is whether the files are writable afterwards.

    The script does several independent things, so a non-zero exit can still
    have achieved the goal — and a zero exit that did not is still a failure.
    """
    with mock.patch.object(system, "can_grant", lambda: True), \
            mock.patch.object(system, "find", lambda name: f"/usr/bin/{name}"), \
            mock.patch.object(
                system.subprocess, "run",
                lambda *a, **k: mock.Mock(returncode=1, stdout="", stderr=""),
            ):
        # Partial failure, but access was gained: a success.
        with mock.patch.object(system, "write_access", lambda: access()):
            assert system.grant_access() == ""

        # Clean exit, but still no access: a failure, and it says why.
        with mock.patch.object(
            system, "write_access", lambda: access(dir_writable=False)
        ):
            assert "backup" in system.grant_access()


def test_grant_falls_back_when_there_is_no_polkit_agent() -> None:
    """No pkexec means the copyable commands are the only route, and the
    service says so rather than failing silently."""
    with mock.patch.object(system, "can_grant", lambda: False):
        problem = system.grant_access()
    assert "pkexec" in problem
    assert "below" in problem


def test_detection_looks_beyond_the_sessions_path() -> None:
    """A GUI app inherits a shorter PATH than a shell, so `which` alone
    misses a broker in /usr/sbin — which is where several distributions put
    it. Detection goes through finder.find, which also checks the usual
    install directories."""
    from ..broker import finder

    calls: list[str] = []

    def fake_find(name: str) -> str:
        calls.append(name)
        return f"/usr/sbin/{name}"

    with mock.patch.object(system, "find", fake_find):
        assert system.describe(sandbox()).installed
        assert system.write_access().tool == "/usr/sbin/mosquitto_passwd"

    assert finder.BROKER in calls
    assert finder.PASSWD_TOOL in calls


def test_account_instructions_are_shown_not_run() -> None:
    steps = system.account_instructions("studio", "SECRET")
    commands = [command for _caption, command in steps]
    assert any("mosquitto_passwd" in c for c in commands)
    assert any("SECRET" in c for c in commands)
    assert all(c.startswith(("sudo", "printf")) for c in commands)


# ---- Not asking for a password on every single edit ----------------------

def test_the_polkit_rule_grants_exactly_one_verb_on_one_unit() -> None:
    """Scope is the whole safety argument, so it is asserted literally.

    A rule that matched the action alone would hand the user blanket control
    of every systemd unit — start, stop, disable, anything — as the price of
    not being asked twice. This one has to match the unit, the verb, the
    user and an active local session all at once.
    """
    rule = system.polkit_rule("alice")
    assert 'action.lookup("unit") == "mosquitto.service"' in rule
    assert 'action.lookup("verb") == "reload"' in rule
    assert 'subject.user == "alice"' in rule
    assert "subject.local && subject.active" in rule
    # YES only — never ADMIN or AUTH_SELF, which would defeat the point, and
    # never a blanket allow on the action id alone.
    assert "polkit.Result.YES" in rule
    assert rule.count("polkit.Result") == 1


def test_reload_silence_is_recorded_not_read_back() -> None:
    """The rule cannot be checked on disk, so Studio records what it did.

    /etc/polkit-1/rules.d is root:polkitd 0750 on a normal system: an
    unprivileged process cannot stat a file inside it, and `Path.exists()`
    raises PermissionError rather than returning False. Reading the
    filesystem would report "no rule" forever — including immediately after
    writing one — which would leave the UI offering a grant that had
    already happened.
    """
    store = sandbox()
    assert not system.reload_is_silent(store), "nothing recorded yet"

    system._record_reload_rule(True, store)
    assert system.reload_is_silent(store)

    system._record_reload_rule(False, store)
    assert not system.reload_is_silent(store)


def test_the_grant_installs_the_rule_in_the_same_prompt() -> None:
    """One authorisation for the whole capability, not one per edit.

    The rule has to ride along on the grant the user already agreed to;
    asking a second time for it would be the same papercut in a new place.
    """
    seen: dict = {}

    def fake_run(command, **kwargs):
        seen["script"] = command[-1]
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(system, "can_grant", lambda: True), \
            mock.patch.object(system, "find", lambda name: f"/usr/bin/{name}"), \
            mock.patch.object(system, "write_access", lambda: access()), \
            mock.patch.object(system.subprocess, "run", fake_run):
        assert system.grant_access() == ""

    script = seen["script"]
    assert str(system.POLKIT_RULE) in script
    assert "mosquitto.service" in script
    # A quoted heredoc, so the rule's own quotes and braces survive the shell.
    assert "<<'DESKBUDDY_EOF'" in script
    # And the chowns are still there — the rule is an addition, not a swap.
    assert "chown" in script


def test_a_missing_polkit_directory_does_not_fail_the_grant() -> None:
    """The rule is a convenience: without it every edit still works, it just
    asks first. A machine with no polkit must not report the grant failed."""
    seen: dict = {}

    def fake_run(command, **kwargs):
        seen["script"] = command[-1]
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(system, "can_grant", lambda: True), \
            mock.patch.object(system, "find", lambda name: f"/usr/bin/{name}"), \
            mock.patch.object(system, "write_access", lambda: access()), \
            mock.patch.object(system.subprocess, "run", fake_run):
        assert system.grant_access() == ""

    # Guarded, so the rule is skipped rather than erroring out.
    assert f"if [ -d {system.POLKIT_RULE.parent} ]" in seen["script"]


def test_the_fallback_instructions_install_the_rule_too() -> None:
    """Both routes have to reach the same end state, or the user who pastes
    commands keeps being prompted with no idea why the button-user is not."""
    steps = system.grant_instructions()
    joined = "\n".join(command for _caption, command in steps)
    assert str(system.POLKIT_RULE) in joined
    assert "mosquitto.service" in joined


# ---- Handing the permission back -----------------------------------------

def test_revoke_is_the_inverse_of_the_grant() -> None:
    """Back to root, and the polkit rule gone with it."""
    seen: dict = {}

    def fake_run(command, **kwargs):
        seen["script"] = command[-1]
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(system, "can_grant", lambda: True), \
            mock.patch.object(system, "find", lambda name: f"/usr/bin/{name}"), \
            mock.patch.object(
                system, "write_access", lambda: access(passwd_writable=False)
            ), \
            mock.patch.object(system.subprocess, "run", fake_run):
        assert system.revoke_access() == ""

    script = seen["script"]
    assert "chown root" in script
    assert str(system.POLKIT_RULE) in script
    assert "rm -f" in script


def test_revoke_touches_nothing_the_grant_did_not_create() -> None:
    """Giving a permission back must not rearrange the rest of the broker.

    Accounts keep working, mosquitto.conf keeps whatever ownership it had,
    and no polkit rule but Studio's own is removed.
    """
    seen: dict = {}

    def fake_run(command, **kwargs):
        seen["script"] = command[-1]
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(system, "can_grant", lambda: True), \
            mock.patch.object(system, "find", lambda name: f"/usr/bin/{name}"), \
            mock.patch.object(
                system, "write_access", lambda: access(passwd_writable=False)
            ), \
            mock.patch.object(system.subprocess, "run", fake_run):
        system.revoke_access()

    script = seen["script"]
    assert str(system.CONFIG_FILE) not in script, "mosquitto.conf is not ours"
    assert "mosquitto_passwd" not in script, "no account is touched"
    # The rule is removed by exact path, never by a glob over the directory.
    assert f"rm -f {system.POLKIT_RULE}" in script
    assert "*" not in script


def test_revoke_succeeds_only_when_access_is_actually_gone() -> None:
    """Same rule as the grant: the filesystem decides, not the exit code."""
    with mock.patch.object(system, "can_grant", lambda: True), \
            mock.patch.object(system, "find", lambda name: f"/usr/bin/{name}"), \
            mock.patch.object(
                system.subprocess, "run",
                lambda *a, **k: mock.Mock(returncode=0, stdout="", stderr=""),
            ):
        # Clean exit but Studio can still write: not a successful revoke.
        with mock.patch.object(system, "write_access", lambda: access()):
            assert system.revoke_access()
        # Access genuinely lost: success.
        with mock.patch.object(
            system, "write_access", lambda: access(dir_writable=False)
        ):
            assert system.revoke_access() == ""


def test_a_dismissed_revoke_prompt_changes_nothing() -> None:
    with mock.patch.object(system, "can_grant", lambda: True), \
            mock.patch.object(system, "find", lambda name: f"/usr/bin/{name}"), \
            mock.patch.object(
                system.subprocess, "run",
                lambda *a, **k: mock.Mock(returncode=126, stdout="", stderr=""),
            ):
        assert "Cancelled" in system.revoke_access()


def test_revoke_instructions_undo_each_grant_instruction() -> None:
    """The paste-it-yourself path has to reach the same place as the button."""
    joined = "\n".join(c for _caption, c in system.revoke_instructions())
    assert str(system.PASSWD_FILE) in joined
    assert str(system.ACL_FILE) in joined
    assert str(system.CONFIG_DIR) in joined
    assert str(system.POLKIT_RULE) in joined
    assert all(
        command.startswith("sudo ")
        for _caption, command in system.revoke_instructions()
    )


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} system broker tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
