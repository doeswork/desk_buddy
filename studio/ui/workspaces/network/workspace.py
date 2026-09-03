"""Network — the workspace, and the broker state its pages share.

The workspace owns what more than one page needs: the broker report, the
account list, and the last command result. Detection shells out to
`mosquitto -h`, so it is read once here rather than by every page that
wants to know whether the broker is up.
"""

from __future__ import annotations

from ....services.network import accounts as account_service
from ....services.network import broker_commands as commands
from ....services.network import report
from ...components import ActionSpec
from ..base import Workspace
from .accounts import AccountsPage
from .add_account import AddAccountPage
from .broker import BrokerPage


class NetworkWorkspace(Workspace):
    key = "network"
    label = "Network"

    page_classes = [BrokerPage, AccountsPage, AddAccountPage]
    # Add Account is reached from a button on Accounts, not from the panel:
    # it is a step in a task, not a place to go.
    link_keys = ["broker", "accounts"]

    def __init__(self) -> None:
        super().__init__()
        self._report = None
        self.last_result = None
        # Set once, on the visit that creates Studio's account.
        self.studio_password = ""
        self.studio_problem = ""

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
        """Every account, with Studio's own guaranteed to be among them.

        Ensuring it here rather than at broker start: this is the one place
        that asks the question, and an account file can also be deleted or
        replaced behind Studio's back between two visits to this list.
        """
        if self.running():
            created, problem = account_service.ensure_studio()
            if created is not None:
                # The one moment this password can be known. Handing it to the
                # page is what lets the user copy it before it is gone.
                self.studio_password = created.password
            elif problem:
                self.studio_problem = problem
        return account_service.accounts()

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
        """
        self.last_result = command()
        self.refresh()
