"""Mosquitto finder tests.

    python -m studio.services.network.tests.tests

The interesting cases are the ones this developer machine cannot show: a box
with no Mosquitto, a split package where only half is present, and a binary
that will not report a version. Those are simulated by pointing the lookup at
an empty directory, so the tests give the same answer on any machine.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from unittest import mock

from ..broker import finder as install
from ..broker.finder import BrokerStatus, Tool


class fake_path:
    """Run a block with PATH replaced by `directory` and no extra dirs.

    Both have to be neutralised: EXTRA_PATHS would otherwise still find the
    real binaries in /usr/sbin on a machine that has them.
    """

    def __init__(self, directory: str) -> None:
        self.directory = directory

    def __enter__(self):
        self._path = os.environ.get("PATH", "")
        self._extra = install.EXTRA_PATHS
        os.environ["PATH"] = self.directory
        install.EXTRA_PATHS = ()
        return self

    def __exit__(self, *exc) -> None:
        os.environ["PATH"] = self._path
        install.EXTRA_PATHS = self._extra


def make_executable(directory: Path, name: str, script: str = "") -> Path:
    """A stand-in binary that prints what we tell it to."""
    path = directory / name
    path.write_text(script or "#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    return path


def test_nothing_installed() -> None:
    with tempfile.TemporaryDirectory() as empty, fake_path(empty):
        status = install.detect()
    assert not status.installed
    assert not status.partial
    assert not status.broker.found
    assert status.summary == "Mosquitto is not installed"


def test_fully_installed() -> None:
    with tempfile.TemporaryDirectory() as bindir:
        directory = Path(bindir)
        make_executable(directory, "mosquitto",
                        "#!/bin/sh\necho 'mosquitto version 2.1.2'\n")
        make_executable(directory, "mosquitto_passwd")
        with fake_path(bindir):
            status = install.detect()
    assert status.installed
    assert not status.partial
    assert status.broker.version == "2.1.2"
    assert status.summary == "Mosquitto 2.1.2"


def test_split_package_broker_without_passwd_tool() -> None:
    """Some distributions ship these separately; that is neither state."""
    with tempfile.TemporaryDirectory() as bindir:
        directory = Path(bindir)
        make_executable(directory, "mosquitto",
                        "#!/bin/sh\necho 'mosquitto version 2.0.18'\n")
        with fake_path(bindir):
            status = install.detect()
    assert not status.installed, "must not claim installed without the passwd tool"
    assert status.partial
    assert "mosquitto_passwd missing" in status.summary


def test_passwd_tool_without_broker() -> None:
    with tempfile.TemporaryDirectory() as bindir:
        make_executable(Path(bindir), "mosquitto_passwd")
        with fake_path(bindir):
            status = install.detect()
    assert not status.installed
    assert status.partial
    assert "broker itself is missing" in status.summary


def test_version_is_read_from_stdout_not_stderr() -> None:
    """`mosquitto -h` also writes a 'terminating' line to stderr; reading the
    merged streams would pick up whichever arrived first."""
    with tempfile.TemporaryDirectory() as bindir:
        directory = Path(bindir)
        make_executable(
            directory, "mosquitto",
            "#!/bin/sh\n"
            "echo 'mosquitto version 2.1.2'\n"
            "echo '123: mosquitto version 9.9.9 terminating' >&2\n",
        )
        make_executable(directory, "mosquitto_passwd")
        with fake_path(bindir):
            status = install.detect()
    assert status.broker.version == "2.1.2", status.broker.version


def test_binary_that_reports_no_version() -> None:
    """Present but silent: still installed, just unlabelled. Never a crash."""
    with tempfile.TemporaryDirectory() as bindir:
        directory = Path(bindir)
        make_executable(directory, "mosquitto", "#!/bin/sh\necho nothing useful\n")
        make_executable(directory, "mosquitto_passwd")
        with fake_path(bindir):
            status = install.detect()
    assert status.installed
    assert status.broker.version == ""
    assert status.summary == "Mosquitto installed"


def test_broken_binary_does_not_raise() -> None:
    """A file that cannot execute must be a status, not a traceback."""
    with tempfile.TemporaryDirectory() as bindir:
        directory = Path(bindir)
        make_executable(directory, "mosquitto", "not a valid script at all")
        make_executable(directory, "mosquitto_passwd")
        with fake_path(bindir):
            status = install.detect()
    assert status.broker.found
    assert status.broker.version == ""


def test_version_pattern_tolerates_two_and_three_part_versions() -> None:
    assert install.VERSION_PATTERN.search("mosquitto version 2.1").group(1) == "2.1"
    assert install.VERSION_PATTERN.search("mosquitto version 2.1.2").group(1) == "2.1.2"


def test_install_command_is_never_empty() -> None:
    """Whatever the platform, the user gets something actionable."""
    command = install.install_command()
    assert command and not command.isspace()


def test_install_advice_survives_a_thin_path() -> None:
    """A GUI app inherits the desktop session's PATH, which is often much
    shorter than a shell's. The advice must not degrade to the generic
    fallback just because the package manager is not on it."""
    with tempfile.TemporaryDirectory() as empty, fake_path(empty):
        command = install.install_command()
    assert "package manager" not in command or os.name == "nt", command


def test_manager_lookup_checks_real_directories() -> None:
    with tempfile.TemporaryDirectory() as empty, fake_path(empty):
        # Whatever this machine has, _has_manager must find it off PATH.
        found = [m for m, _ in install._LINUX_MANAGERS if install._has_manager(m)]
    assert found or os.name == "nt", "no package manager found off PATH"


def test_status_is_readable_without_a_version() -> None:
    status = BrokerStatus(broker=Tool("mosquitto"), passwd_tool=Tool("mosquitto_passwd"))
    assert status.summary == "Mosquitto is not installed"


def test_port_open_is_false_for_a_dead_port() -> None:
    """Picked from the ephemeral range and never bound, so nothing answers."""
    assert not install.port_open(59999)


def test_port_open_finds_a_real_listener() -> None:
    import socket
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert install.port_open(port)
    finally:
        server.close()


# ---- The strings the UI renders ----------------------------------------
# The point of these: a view should never have to branch on `partial` or
# assemble a sentence. If the wording is wrong, it is wrong here.


def report_for(broker: bool, passwd: bool, port: int) -> install.BrokerReport:
    return install.BrokerReport(
        status=install.BrokerStatus(
            broker=Tool("mosquitto", "/usr/bin/mosquitto" if broker else "", "2.1.2" if broker else ""),
            passwd_tool=Tool("mosquitto_passwd", "/usr/bin/mosquitto_passwd" if passwd else ""),
        ),
        port=port,
    )


def test_report_running_ours() -> None:
    r = report_for(True, True, 1883)
    assert r.running and r.installed
    assert r.headline == "Mosquitto 2.1.2 — running"
    assert "listening on port 1883" in r.detail
    assert r.install_command == "", "nothing to install when it is running"
    assert r.chip == "● broker on 1883"


def test_report_installed_but_stopped() -> None:
    r = report_for(True, True, 0)
    assert r.installed and not r.running
    assert r.headline == "Mosquitto 2.1.2"
    assert "Not running yet" in r.detail
    assert "mosquitto" in r.detail, "must name the service to start"
    assert r.install_command == ""
    assert r.chip == install.NO_BROKER_CHIP


def test_report_not_installed_offers_a_command() -> None:
    r = report_for(False, False, 0)
    assert not r.installed
    assert r.headline == "Mosquitto is not installed"
    assert r.install_command, "must tell the user what to run"


def test_report_split_package_is_not_installed() -> None:
    r = report_for(True, False, 0)
    assert not r.installed
    assert "mosquitto_passwd missing" in r.headline
    assert r.install_command, "still needs an install command"


def test_chip_wording_has_one_source() -> None:
    """chip_text() and BrokerReport.chip must never drift apart.

    They did once: BrokerReport.chip dropped an argument on the way to
    chip_for(), so the bar and the page disagreed about the same broker.
    """
    from ..broker import system

    down = system.SystemBroker(
        host="", port=1883, reachable=False, user="", has_password=False,
        installed=True, service_active=False,
    )
    original = system.describe
    system.describe = lambda *a, **k: down
    try:
        assert install.chip_text() == report_for(True, True, 0).chip
    finally:
        system.describe = original


def test_chip_says_where_the_broker_is() -> None:
    assert install.chip_for(0) == install.NO_BROKER_CHIP
    assert install.chip_for(1883) == "● broker on 1883"


# ---- Firewall detection --------------------------------------------------

def test_firewall_hint_matches_the_detected_firewall() -> None:
    """The command has to be the one that works for *that* firewall."""
    assert "ufw allow" in install.firewall_hint(18830, "ufw")
    assert "18830" in install.firewall_hint(18830, "ufw")
    assert "firewall-cmd" in install.firewall_hint(18830, "firewalld")
    # An unknown or absent firewall has no command to offer, and must not
    # invent one — a wrong sudo command is worse than none.
    assert install.firewall_hint(18830, "") == ""
    assert install.firewall_hint(18830, "pf") == ""


def test_active_firewall_reports_a_name_or_nothing() -> None:
    """Never raises, whatever the host looks like — it runs on every build
    of the Broker page, and a machine without systemd must not break it."""
    assert install.active_firewall() in ("", "ufw", "firewalld")


# ---- Reading the firewall's rules ---------------------------------------
# `ufw status` needs root; the file it reads does not. These use a real
# rules file in a temp dir rather than the machine's own, so the suite
# passes the same way on a developer box with no firewall at all.

RULES = """*filter
:ufw-user-input - [0:0]
### RULES ###

### tuple ### allow tcp 1883 0.0.0.0/0 any 192.168.0.0/16 in
-A ufw-user-input -p tcp --dport 1883 -s 192.168.0.0/16 -j ACCEPT

### tuple ### allow tcp 8080 0.0.0.0/0 any 0.0.0.0/0 in
-A ufw-user-input -p tcp --dport 8080 -j ACCEPT

### tuple ### allow tcp 9000:9100 0.0.0.0/0 any 0.0.0.0/0 in
-A ufw-user-input -p tcp --dport 9000:9100 -j ACCEPT

### tuple ### allow udp 5353 0.0.0.0/0 any 0.0.0.0/0 in
-A ufw-user-input -p udp --dport 5353 -j ACCEPT

### tuple ### allow tcp 2222 0.0.0.0/0 any 0.0.0.0/0 out
-A ufw-user-output -p tcp --dport 2222 -j ACCEPT
### END RULES ###
COMMIT
"""


def with_ufw(rules: str = RULES, *, enabled: bool = True, policy: str = "DROP"):
    """Point the reader at a throwaway ufw config. Used as a context manager."""
    import tempfile

    directory = Path(tempfile.mkdtemp())
    (directory / "user.rules").write_text(rules)
    (directory / "ufw.conf").write_text(
        f"ENABLED={'yes' if enabled else 'no'}\n"
    )
    (directory / "default").write_text(f'DEFAULT_INPUT_POLICY="{policy}"\n')
    return mock.patch.multiple(
        install,
        UFW_RULES=directory / "user.rules",
        UFW_CONF=directory / "ufw.conf",
        UFW_DEFAULTS=directory / "default",
        active_firewall=lambda: "ufw",
    )


def test_an_allowed_port_is_read_from_the_rules() -> None:
    """The whole point: this used to be assumed unknowable without root."""
    with with_ufw():
        verdict = install.firewall_allows(1883)
    assert verdict.allowed and verdict.known
    assert not verdict.blocked
    # The matching rule is quoted back, so the card can show its reasoning.
    assert "1883" in verdict.rule
    assert "192.168.0.0/16" in verdict.rule


def test_a_port_with_no_rule_is_blocked() -> None:
    with with_ufw():
        verdict = install.firewall_allows(1884)
    assert verdict.known
    assert not verdict.allowed
    assert verdict.blocked


def test_a_port_range_covers_the_ports_inside_it() -> None:
    with with_ufw():
        assert install.firewall_allows(9000).allowed
        assert install.firewall_allows(9050).allowed
        assert install.firewall_allows(9100).allowed
        assert install.firewall_allows(9101).blocked


def test_only_inbound_tcp_rules_count() -> None:
    """A UDP rule or an outbound one does not let a robot in.

    Counting either would produce a false "allowed", which is worse than no
    answer: it sends the user looking for a fault somewhere else entirely.
    """
    with with_ufw():
        assert install.firewall_allows(5353).blocked   # udp only
        assert install.firewall_allows(2222).blocked   # outbound only


def test_a_disabled_firewall_blocks_nothing() -> None:
    with with_ufw(enabled=False):
        verdict = install.firewall_allows(1884)
    assert verdict.allowed
    assert not verdict.blocked


def test_a_default_accept_policy_blocks_nothing() -> None:
    """A firewall that accepts by default drops nothing, so an absent rule
    is not a problem worth reporting."""
    with with_ufw(policy="ACCEPT"):
        verdict = install.firewall_allows(1884)
    assert verdict.allowed
    assert not verdict.blocked


def test_unreadable_rules_are_unknown_never_blocked() -> None:
    """The distinction that keeps this honest: "could not tell" is not
    "blocked". Claiming a block Studio cannot see sends the user to fix
    something that may be fine."""
    with with_ufw() as _:
        with mock.patch.object(install, "UFW_RULES", Path("/nonexistent/rules")):
            verdict = install.firewall_allows(1883)
    assert verdict.firewall == "ufw"
    assert not verdict.known
    assert not verdict.allowed
    assert not verdict.blocked, "unknown must never present as blocked"


def test_firewalld_is_reported_as_unknown() -> None:
    """Its state spans zones, runtime-vs-permanent rules and interfaces.
    Guessing wrong is worse than saying so, so it is deliberately not
    parsed."""
    with mock.patch.object(install, "active_firewall", lambda: "firewalld"):
        verdict = install.firewall_allows(1883)
    assert verdict.firewall == "firewalld"
    assert not verdict.known
    assert not verdict.blocked


def test_no_firewall_means_no_verdict() -> None:
    with mock.patch.object(install, "active_firewall", lambda: ""):
        verdict = install.firewall_allows(1883)
    assert verdict.firewall == ""
    assert not verdict.blocked


def test_firewall_allows_never_raises_on_this_machine() -> None:
    """It runs on every Broker page build, against whatever this host has."""
    verdict = install.firewall_allows(1883)
    assert verdict.firewall in ("", "ufw", "firewalld")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} broker finder tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
