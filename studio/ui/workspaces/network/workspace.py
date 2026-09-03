"""Network — the workspace, and the broker state its pages share.

The workspace owns what more than one page needs: the broker report, the
account list, and the last command result. Detection shells out to
`mosquitto -h`, so it is read once here rather than by every page that
wants to know whether the broker is up.
"""

from __future__ import annotations

from ....models.mqtt_users import users
from ....services.network import broker_commands as commands
from ....services.network import report
from ....services.network import sync_accounts
from ...components import ActionSpec
from ..base import Workspace
from .accounts import AccountsPage
from .add_account import AddAccountPage
from .broker import BrokerPage
from .edit_account import EditAccountPage
from .topics import TopicsPage


class NetworkWorkspace(Workspace):
    key = "network"
    label = "Network"

    page_classes = [
        BrokerPage,
        AccountsPage,
        TopicsPage,
        AddAccountPage,
        EditAccountPage,
    ]
    # Add Account and Edit Account are reached from buttons on Accounts, not
    # from the panel: both are a step in a task, not a place to go.
    link_keys = ["broker", "accounts", "topics"]

    def __init__(self) -> None:
        super().__init__()
        self._report = None
        self.last_result = None
        # Set when applying records to the broker fails, so the page can say
        # so rather than looking as though the change took.
        self.account_problem = ""

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

    def _run(self, command) -> None:
        """Run a broker command, then show whatever it says.

        The result is kept rather than discarded: a failure to start is the
        one thing the user most needs to read, and it is usually Mosquitto's
        own message about why.

        A broker that just came up is handed the account records immediately:
        it was started from config files that may be older than them, or
        absent entirely on a machine where the broker directory was cleared.
        """
        self.last_result = command()
        self.apply_to_broker()
        self.refresh()
