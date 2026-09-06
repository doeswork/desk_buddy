"""Network — the workspace, and the broker state its pages share.

The workspace owns what more than one page needs: the broker's state, the
account list, and whether Studio is allowed to change it. Detection shells
out to `mosquitto -h` and probes a socket, so it is read once here rather
than by every page that wants to know whether the broker is up.

Network activation checks and prepares the machine's Mosquitto asynchronously.
Pages render shared progress; privileged changes use system authorization.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QTimer

from ....storage import keys
from ....storage.settings import settings
from ....services.network import report
from ....services.network.broker import system
from ....services.network.broker.setup import Coordinator, SetupStatus
from ..base import Workspace
from .accounts import AccountsPage
from .add_account import AddAccountPage
from .broker import BrokerPage
from .edit_account import EditAccountPage
from .robots import RobotsPage
from .test_message import TestMessagePage
from .topics import TopicsPage


class NetworkWorkspace(Workspace):
    key = "network"
    label = "Network"

    page_classes = [
        BrokerPage,
        AccountsPage,
        RobotsPage,
        TopicsPage,
        TestMessagePage,
        AddAccountPage,
        EditAccountPage,
    ]
    # Add Account and Edit Account are reached from buttons on Accounts, not
    # from the panel: both are a step in a task, not a place to go.
    link_keys = ["broker", "accounts", "robots", "topics", "test_message"]

    def __init__(self) -> None:
        super().__init__()
        self._report = None
        # Set when a change to the broker's accounts fails, so the page can
        # say so rather than looking as though the change took.
        self.account_problem = ""
        # The broker's state, read once per build: describe() probes a
        # socket and shells out to systemctl.
        self._system = None
        # Cached alongside it — write_access() stats three paths.
        self._access = None
        # Why the last "Check connection" failed, so the Broker page can say
        # so instead of silently looking unchanged.
        self.verify_problem = ""
        # Why the last "Grant Access" did not take — including the user
        # simply dismissing the password prompt, which is an answer rather
        # than an error, and is reported in those terms.
        self.grant_problem = ""
        self.setup = Coordinator()
        self._setup_timer = None
        self.advanced_open = False

    def activate(self) -> None:
        """Check/setup MQTT after an explicit Network visit or launch hook."""
        if self.setup.status.state == "ready" and not self.setup.running:
            self.setup.attempted = False
        self.start_setup()

    def start_setup(self, *, retry: bool = False, action: str = "setup") -> None:
        store = settings()
        if self.setup.running or (self.setup.attempted and not retry):
            return
        if store.get(keys.SYSTEM_BROKER_SETUP_PAUSED) and not retry:
            self.setup.status = SetupStatus("cancelled", "Automatic setup is paused. Retry setup to enable it.")
            self.find("broker").rebuild()
            return
        name, password = system.suggested_account(store)
        request = dict(uid=os.getuid() if hasattr(os, "getuid") else 0,
                       user=name, password=password, port=system.port(store),
                       host=store.get(keys.SYSTEM_BROKER_HOST), action=action,
                       reload_pending=store.get(keys.SYSTEM_BROKER_RELOAD_PENDING),
                       robot_host=store.get(keys.SYSTEM_BROKER_ROBOT_HOST),
                       network_ready=store.get(keys.SYSTEM_BROKER_NETWORK_READY))
        if not self.setup.start(request, retry=retry):
            return
        store.set(keys.SYSTEM_BROKER_SETUP_PAUSED, action == "revoke")
        store.set(keys.SYSTEM_BROKER_VERIFIED, False)
        store.sync()
        if self._setup_timer is None:
            self._setup_timer = QTimer()
            self._setup_timer.setInterval(100)
            self._setup_timer.timeout.connect(self._poll_setup)
        self._setup_timer.start()
        self.find("broker").rebuild()

    def _poll_setup(self) -> None:
        if not self.setup.poll():
            return
        if self.setup.running:
            self.find("broker").rebuild()
            return
        self._setup_timer.stop()
        state = self.setup.status
        store = settings()
        if state.user:
            store.set(keys.SYSTEM_BROKER_USER, state.user)
            system._record_password(state.user, store.get(keys.SYSTEM_BROKER_PASSWORD))
        store.set(keys.SYSTEM_BROKER_VERIFIED, state.connected)
        store.set(keys.SYSTEM_BROKER_ROBOT_HOST, state.robot_host)
        store.set(keys.SYSTEM_BROKER_NETWORK_READY, state.network_ready)
        store.set(keys.SYSTEM_BROKER_SETUP_PAUSED, state.state in ("failed", "cancelled"))
        if state.reload_rule:
            store.set(keys.SYSTEM_BROKER_RELOAD_RULE, True)
        if state.access_revoked:
            store.set(keys.SYSTEM_BROKER_RELOAD_RULE, False)
        if state.connected and not state.access_revoked:
            store.set(keys.SYSTEM_BROKER_RELOAD_PENDING, False)
        store.sync()
        self.refresh()
        from ....services.network.pub_sub.client import mqtt_client
        mqtt_client().reconcile()
        self.say(state.message)

    def broker_host(self) -> str:
        """The host the running broker actually listens on, or "" if it is
        not running. What anything that wants to *reach* the broker — the
        MQTT provisioning dialog, say — should read."""
        from ....services.network.broker.finder import robot_endpoint
        return robot_endpoint()[0]

    # ---- shared state ----------------------------------------------------
    def broker(self):
        """The broker's state, read once and cached.

        Detection shells out to `mosquitto -h`, so it is not something to
        repeat on every context-bar rebuild.
        """
        if self._report is None:
            self._report = report()
        return self._report

    def accounts(self) -> list:
        """Every account on the broker, read from its own ACL file.

        The broker is the source of truth now, not a record file of Studio's.
        That is the whole point of using the machine's Mosquitto: accounts
        created with `mosquitto_passwd` by hand, or by anything else on the
        system, are as real as ones Studio made and belong in this list.
        """
        return system.accounts()

    def running(self) -> bool:
        """Whether the machine's broker is up.

        The system service continues running independently of Studio.
        """
        return self.system_broker().reachable

    def write_access(self):
        """Whether Studio may edit the broker's accounts, cached per build."""
        if self._access is None:
            self._access = system.write_access()
        return self._access

    def may_edit_accounts(self) -> bool:
        return self.write_access().allowed

    def recheck_access(self) -> bool:
        """Stat the account files again. True when the answer changed.

        Permissions are the one part of this state a user routinely changes
        from outside Studio — a chown in a terminal, exactly the commands
        the Broker page prints. Nothing in the app can observe that, so a
        cached "not allowed" survives the fix and the page goes on offering
        instructions for work already done.

        Cheap enough to call on the way into a page: three `os.access`
        calls and a PATH lookup, no subprocess.
        """
        before = self._access.allowed if self._access is not None else None
        self._access = system.write_access()
        return before is not None and before != self._access.allowed

    def grant_access(self) -> str:
        """Ask the system for write access to the broker's account files.

        "" on success. The workspace's caches go either way: a refused grant
        can still have been preceded by a change made in a terminal, and a
        successful one has certainly changed what every page should show.
        """
        self.grant_problem = system.grant_access()
        self.refresh()
        self.say(
            f"Not granted — {self.grant_problem}"
            if self.grant_problem
            else "Studio can now manage this broker's users"
        )
        return self.grant_problem

    def revoke_access(self) -> str:
        """Hand the broker's account files back to root. "" on success.

        Shares `grant_problem`, because from the page's point of view both
        are "the last thing you asked about permissions, and what came of
        it" — and only one of them can be the last thing.
        """
        if self.setup.running:
            return "Wait for setup to finish before revoking access."
        self.setup.paused = True
        settings().set(keys.SYSTEM_BROKER_SETUP_PAUSED, True)
        settings().sync()
        self.start_setup(retry=True, action="revoke")
        return ""

    def create_account(self, name: str, password: str, topics: str) -> str:
        """Add one account to the broker. "" on success, else why not."""
        self.account_problem = system.create_account(name, password, topics)
        self.refresh()
        return self.account_problem

    def remove_account(self, name: str) -> str:
        """Delete one account from the broker. "" on success."""
        self.account_problem = system.remove_account(name)
        self.refresh()
        return self.account_problem

    def recheck_connection(self) -> None:
        """Drop the cached broker state so the next read reconnects.

        Only when it has not already worked: a verified account is not
        re-tested on every visit, so this costs nothing in the common case
        and about a second in the one where the user has just finished the
        setup and wants to see it take.
        """
        if self._system is not None and not self._system.configured:
            self._system = None

    def verify_system_account(self) -> None:
        """Test the recorded credentials against the broker, and say so.

        Success used to be silent: `verify()` returned "", nothing rendered
        it, and the click was indistinguishable from doing nothing — which
        is why a working connection could look like a failed one. Both
        outcomes now reach the status strip.
        """
        self.verify_problem = system.verify()
        self.refresh()

        if self.verify_problem:
            self.say(f"Not connected — {self.verify_problem}")
            return
        broker = self.system_broker()
        self.say(
            f"Connected as “{broker.user}” — {broker.host}:{broker.port}"
        )

    def system_broker(self):
        """The broker's state, cached for the length of one build."""
        if self._system is None:
            self._system = system.describe(connect=False)
        return self._system

    def refresh(self) -> None:
        """Drop the cached answer and rebuild every page."""
        self._report = None
        self._system = None
        self._access = None
        super().refresh()

    # ---- BAR 2 -----------------------------------------------------------
    def build_actions(self) -> list:
        """Nothing. Network has no workspace-wide verbs.

        There were two, and both were the wrong shape:

        "Check Connection" existed because Studio waited to be asked before
        authenticating. It connects on its own now, so a button to make it
        do the thing it already does is a button that only asks the user to
        confirm what the page in front of them already says.

        Grant and Revoke are setup, not everyday work — closer to installing
        the broker than to sending a message. Setup belongs on the Broker
        page beside the explanation of what it does, not on a bar that
        follows the user onto every other page.
        """
        return []

    def add_account(self) -> None:
        """Reached from the Add User button atop the Accounts page."""
        self.go_to("add_account")

    def start_on_startup(self) -> None:
        """Kept as a no-op so main_window's launch path needs no branch.

        Studio used to start a broker of its own here. It no longer runs
        one: the machine's Mosquitto is already up, or it is a setup task
        the Broker page explains, and neither is something to do behind the
        user's back at launch.
        """
        return
