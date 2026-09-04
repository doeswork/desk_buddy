"""Network — the workspace, and the broker state its pages share.

The workspace owns what more than one page needs: the broker report, the
account list, and the last command result. Detection shells out to
`mosquitto -h`, so it is read once here rather than by every page that
wants to know whether the broker is up.
"""

from __future__ import annotations

from ....models.config.mqtt_users import users
from ....services.network import broker_commands as commands
from ....services.network import lan_address, report
from ....services.network import sync_accounts
from ...components import ActionSpec
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
        self.last_result = None
        # Set when applying records to the broker fails, so the page can say
        # so rather than looking as though the change took.
        self.account_problem = ""
        # Off by default: a robot on the network cannot reach 127.0.0.1 on
        # this machine, so exposing the broker beyond localhost is a
        # deliberate choice the user makes on the Broker page, not a
        # default — see services/network/broker/commands.py's DEFAULT_HOST.
        self._bind_lan = False
        # The host the *running* broker was actually started on — separate
        # from the toggle above, which is next start's intent. A toggle
        # flipped after the broker is already up must not claim a host it
        # is not really listening on.
        self._bound_host = ""

    # ---- LAN bind choice ---------------------------------------------------
    @property
    def bind_lan(self) -> bool:
        return self._bind_lan

    def set_bind_lan(self, value: bool) -> None:
        if value == self._bind_lan:
            return
        self._bind_lan = value
        self.refresh()

    def broker_host(self) -> str:
        """The host the running broker actually listens on, or "" if it is
        not running. What anything that wants to *reach* the broker — the
        MQTT provisioning dialog, say — should read, instead of assuming a
        host from the toggle above."""
        return self._bound_host if self.running() else ""

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
        """Every account on record, with Studio's own guaranteed among them.

        Read from Studio's own records rather than from the broker, so the
        list is the same whether the broker is up, down, or has never been
        installed. Studio's account is ensured here because this is the one
        place that asks the question — and it is created once, ever: the
        record keeps its password, so a second launch finds it rather than
        minting a new one.
        """
        users().ensure_studio()
        return users().all()

    def apply_to_broker(self) -> None:
        """Push the records onto a running broker, and say so if that failed.

        Every account change calls this. When the broker is down it is a
        no-op that costs nothing — the records are already saved, and the
        next start writes them out.
        """
        if not self.running():
            return
        self.account_problem = sync_accounts()

    def running(self) -> bool:
        """Whether the broker Studio manages is up.

        The question is whether *ours* is running, not whether any broker is.
        A system broker on 1883 is someone else's — Studio can neither stop it
        nor claim it.
        """
        return commands.is_ours()

    def refresh(self) -> None:
        """Drop the cached answer and rebuild every page."""
        self._report = None
        super().refresh()

    # ---- BAR 2 -----------------------------------------------------------
    def build_actions(self) -> list:
        """Every Network button, on every Network page.

        Only the broker controls: what an account may do is a property of one
        row in a list on the Accounts page, not of the workspace, so those
        live there instead — see AccountsPage and AccountTable.
        """
        installed = self.broker().installed
        running = installed and self.running()

        return [
            ActionSpec(
                "Start Broker",
                on_click=self.start_broker if installed and not running else None,
            ),
            ActionSpec(
                "Stop Broker",
                on_click=self.stop_broker if running else None,
            ),
            ActionSpec(
                "Restart Broker",
                on_click=self.restart_broker if running else None,
            ),
        ]

    def add_account(self) -> None:
        """Reached from the Add Account button atop the Accounts page."""
        self.go_to("add_account")

    # ---- broker actions --------------------------------------------------
    # They live on the workspace because their result changes both pages: a
    # broker that just started is what gives Accounts anything to show.
    def start_broker(self) -> None:
        self._run(commands.start)

    def stop_broker(self) -> None:
        self._run(commands.stop)

    def restart_broker(self) -> None:
        self._run(commands.restart)

    def _chosen_host(self) -> str:
        """The host to start on, resolving the toggle to an address.

        Falls back to localhost-only if the LAN toggle is on but no LAN
        address could be found (no network) — the broker still starts and
        is still reachable from this machine, just not from a robot, which
        is a better failure than not starting at all.
        """
        if self._bind_lan:
            address = lan_address()
            if address:
                return address
        return commands.DEFAULT_HOST

    def _run(self, command) -> None:
        """Run a broker command, then show whatever it says.

        The result is kept rather than discarded: a failure to start is the
        one thing the user most needs to read, and it is usually Mosquitto's
        own message about why.

        A broker that just came up is handed the account records immediately:
        it was started from config files that may be older than them, or
        absent entirely on a machine where the broker directory was cleared.
        """
        host = self._chosen_host()
        # start/restart take (port, host); stop takes neither. Calling every
        # command the same way would pass stop() a host it does not accept.
        self.last_result = (
            command(host=host) if command in (commands.start, commands.restart)
            else command()
        )
        self._bound_host = host if self.last_result else ""
        self.apply_to_broker()
        self.refresh()
