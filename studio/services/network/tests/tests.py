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


def test_running_port_prefers_ours_over_the_system_one() -> None:
    """If both are up, the broker Studio started is the one it manages."""
    seen = []

    def fake_open(port, host="127.0.0.1"):
        seen.append(port)
        return True

    original = install.port_open
    install.port_open = fake_open
    try:
        assert install.running_port() == install.DEFAULT_PORT
    finally:
        install.port_open = original
    assert seen[0] == install.DEFAULT_PORT, "ours must be checked first"


def test_running_port_is_zero_when_nothing_listens() -> None:
    original = install.port_open
    install.port_open = lambda port, host="127.0.0.1": False
    try:
        assert install.running_port() == 0
    finally:
        install.port_open = original


def test_our_port_is_not_the_mosquitto_default() -> None:
    """Studio must never collide with a broker the user already runs."""
    assert install.DEFAULT_PORT != install.SYSTEM_PORT


# ---- The strings the UI renders ----------------------------------------
# The point of these: a view should never have to branch on `partial` or
# assemble a sentence. If the wording is wrong, it is wrong here.


def report_for(broker: bool, passwd: bool, port: int, ours: bool = True) -> install.BrokerReport:
    return install.BrokerReport(
        status=install.BrokerStatus(
            broker=Tool("mosquitto", "/usr/bin/mosquitto" if broker else "", "2.1.2" if broker else ""),
            passwd_tool=Tool("mosquitto_passwd", "/usr/bin/mosquitto_passwd" if passwd else ""),
        ),
        port=port,
        ours=ours,
    )


def test_report_running_ours() -> None:
    r = report_for(True, True, 18830, ours=True)
    assert r.running and r.installed
    assert r.headline == "Mosquitto 2.1.2 — running"
    assert "Studio's broker is listening on port 18830" in r.detail
    assert r.install_command == "", "nothing to install when it is running"
    assert r.chip == "● broker on 18830"


def test_report_running_but_not_ours() -> None:
    """The bug that shipped: a system broker on 1883 made the page claim it was
    running and left the action bar empty, with no way to start our own."""
    r = report_for(True, True, 1883, ours=False)
    assert r.running, "something IS listening"
    assert not r.ours
    assert "another broker" in r.headline
    assert "did not start" in r.detail
    assert "not ours" in r.chip
    assert str(install.DEFAULT_PORT) in r.detail, "must say where ours would go"


def test_report_installed_but_stopped() -> None:
    r = report_for(True, True, 0, ours=False)
    assert r.installed and not r.running
    assert r.headline == "Mosquitto 2.1.2"
    assert "Not running yet" in r.detail
    assert r.install_command == ""
    assert r.chip == install.NO_BROKER_CHIP


def test_report_not_installed_offers_a_command() -> None:
    r = report_for(False, False, 0, ours=False)
    assert not r.installed
    assert r.headline == "Mosquitto is not installed"
    assert r.install_command, "must tell the user what to run"


def test_report_split_package_is_not_installed() -> None:
    r = report_for(True, False, 0, ours=False)
    assert not r.installed
    assert "mosquitto_passwd missing" in r.headline
    assert r.install_command, "still needs an install command"


def test_chip_wording_has_one_source() -> None:
    """chip_text() and BrokerReport.chip must never drift apart."""
    original = install.running_port
    install.running_port = lambda: 0
    try:
        assert install.chip_text() == report_for(True, True, 0, ours=False).chip
    finally:
        install.running_port = original


def test_chip_marks_a_broker_that_is_not_ours() -> None:
    assert install.chip_for(0, False) == install.NO_BROKER_CHIP
    assert install.chip_for(18830, True) == "● broker on 18830"
    assert "not ours" in install.chip_for(1883, False)


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


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} broker finder tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
