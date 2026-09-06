"""What the Network workspace puts on screen, for the states it can be in.

Every bug this module exists to prevent was a *shape* bug rather than a
logic one: a button that vanished the moment it worked, two buttons for one
setting, a wall of commands for a step already finished, one IP address
repeated across three cards. So these assert what is on the page and what
is not — which controls exist, and how loudly.

    QT_QPA_PLATFORM=offscreen python -m studio.ui.workspaces.network.tests
"""

from __future__ import annotations

import os
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ....services.network.broker import system
from .workspace import NetworkWorkspace

_app = QApplication.instance() or QApplication([])


def workspace(
    *,
    allowed: bool = True,
    silent: bool = True,
    can_grant: bool = True,
    running: bool = True,
) -> NetworkWorkspace:
    """A Network workspace with the broker state pinned to one case."""
    space = NetworkWorkspace()
    space._system = system.SystemBroker(
        host="192.168.16.110",
        port=1883,
        reachable=running,
        user="studio",
        has_password=True,
        installed=True,
        service_active=True,
        verified=True,
    )
    space._access = system.WriteAccess(
        passwd_writable=allowed,
        acl_writable=allowed,
        dir_writable=allowed,
        tool="/usr/bin/mosquitto_passwd",
        files_exist=True,
    )
    return space


CASES = [
    dict(allowed=True, silent=True, can_grant=True, running=True),
    dict(allowed=True, silent=False, can_grant=True, running=True),
    dict(allowed=False, silent=False, can_grant=True, running=True),
    dict(allowed=False, silent=False, can_grant=False, running=True),
    dict(allowed=True, silent=True, can_grant=True, running=False),
]


# ---- The page's shape ----------------------------------------------------

def page(space: NetworkWorkspace, *, silent: bool = True, can_grant: bool = True):
    from PySide6.QtWidgets import QLabel, QPushButton

    with mock.patch.object(system, "write_access", lambda: space._access), \
            mock.patch.object(system, "reload_is_silent", lambda *a: silent), \
            mock.patch.object(system, "can_grant", lambda: can_grant):
        broker = space.find("broker")
        broker._widget = None
        widget = broker.widget()
    return (
        [l.text() for l in widget.findChildren(QLabel)
         if l.objectName() == "CardTitle"],
        [b.text() for b in widget.findChildren(QPushButton) if b.text() != "Copy"],
        [l.text() for l in widget.findChildren(QLabel)
         if l.objectName() == "RowAction"],
    )


def test_the_bar_has_no_network_wide_verbs() -> None:
    """Both of the ones it had were the wrong shape.

    Check Connection asked the user to make Studio do what it now does on
    its own. Grant and Revoke are setup, which belongs beside the
    explanation on the Broker page rather than following the user onto
    every other one.
    """
    for case in CASES:
        assert workspace(**case).build_actions() == []


def test_a_granted_permission_is_one_line_not_a_wall_of_commands() -> None:
    """Setup that is finished stops shouting.

    The card used to keep a full block of revoke commands on screen
    permanently, which is what made a completed step look like an
    outstanding one.
    """
    titles, buttons, links = page(workspace(allowed=True))
    assert not any("Let Studio manage" in t for t in titles)
    assert "Grant Access" not in buttons
    assert links == ["Revoke"], links


def test_an_ungranted_permission_gets_the_full_treatment() -> None:
    """Unfinished setup is the case worth explaining: what is wrong, the
    button that fixes it, and the commands it will run."""
    titles, buttons, links = page(workspace(allowed=False), silent=False)
    assert any("Let Studio manage" in t for t in titles)
    assert "Grant Access" in buttons
    assert links == [], "nothing to revoke when nothing is granted"


def test_no_pkexec_leaves_only_the_commands() -> None:
    """Without a polkit agent there is no button that could work, so the
    card offers the commands and says so rather than a dead control."""
    _titles, buttons, _links = page(
        workspace(allowed=False), silent=False, can_grant=False
    )
    assert "Grant Access" not in buttons

    _titles, _buttons, links = page(
        workspace(allowed=True), can_grant=False
    )
    assert links == []


def test_the_address_is_not_repeated_across_cards() -> None:
    """Three cards naming one IP is how a page stops being read."""
    titles, _buttons, _links = page(workspace(allowed=True))
    assert sum("reach this broker" in t for t in titles) == 0
    assert any(t.startswith("Broker running on") for t in titles), titles


def test_a_broker_that_is_down_says_so_and_offers_the_fix() -> None:
    titles, _buttons, _links = page(workspace(running=False))
    assert "Broker not running" in titles
    assert any("Start the broker" in t for t in titles)


# ---- Saying what happened ------------------------------------------------

def test_a_successful_check_is_reported() -> None:
    """The regression this whole module exists for.

    `verify()` returning "" used to render nothing at all, so a connection
    that worked was indistinguishable from a click that did nothing.
    """
    said: list[str] = []
    space = workspace()
    space.announce = said.append

    with mock.patch.object(system, "verify", lambda *a: ""):
        space.verify_system_account()

    assert said, "a successful check must say so"
    assert "studio" in said[0]
    assert "192.168.16.110:1883" in said[0]


def test_a_failed_check_says_why() -> None:
    said: list[str] = []
    space = workspace()
    space.announce = said.append

    with mock.patch.object(
        system, "verify", lambda *a: "The broker refused the account."
    ):
        space.verify_system_account()

    assert said and "refused" in said[0]


def test_granting_and_revoking_both_report() -> None:
    said: list[str] = []
    space = workspace()
    space.announce = said.append

    with mock.patch.object(system, "grant_access", lambda: ""):
        space.grant_access()
    with mock.patch.object(system, "revoke_access", lambda: ""):
        space.revoke_access()

    assert len(said) == 2, said
    assert "manage" in said[0]
    assert "root" in said[1]


def test_a_workspace_with_no_window_says_nothing() -> None:
    """`announce` is only set once a window adopts the workspace, and the
    tests and the smoke test build workspaces on their own."""
    space = workspace()
    assert space.announce is None
    with mock.patch.object(system, "verify", lambda *a: ""):
        space.verify_system_account()  # must not raise


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} network workspace tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
