"""Network — the layout for this page's sections.

The application layout, in the Rails sense: it declares the page's identity,
its actions and its side panel, then stacks the sections in order. Each section
is its own file and builds itself; this file never reaches inside one.

    broker.py     is Mosquitto installed, and running?
    accounts.py   who may connect, and which topics they may use

Coming with the later steps in mosquitto_plan: robot.py (heartbeat),
traffic.py (the live log).
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from ....network import accounts as account_service
from ....network import broker_commands as commands
from ....network import report
from ...components import ActionSpec, Column, Separator, SidePanel
from ..base import Page
from .accounts import CredentialsDialog, accounts_card, ask_for_name
from .broker import broker_card


class NetworkPage(Page):
    key = "network"
    label = "Network"

    title = "Network"
    subtitle = "The MQTT hub. Robot, models, and workflows all talk through it."

    def __init__(self) -> None:
        super().__init__()
        self._report = None
        self.last_result = None
        self.selected_account = ""

    def broker(self):
        """The broker's state, read once per page build.

        Detection shells out to `mosquitto -h`, so it is not something to
        repeat on every context-bar rebuild.
        """
        if self._report is None:
            self._report = report()
        return self._report

    def accounts(self) -> list:
        return account_service.accounts()

    def refresh(self) -> None:
        """Drop the cached answer and rebuild, after something changed."""
        self._report = None
        self.rebuild()

    def refresh_all(self) -> None:
        """Rebuild the body and the account list together.

        The side panel is cached by the base class, so an account change has to
        drop it explicitly or the list keeps showing what used to be there.
        """
        self._side = None
        self.refresh()

    # ---- the page's own chrome ------------------------------------------
    def build_actions(self) -> list:
        """Offer only what this state can lead to.

        The question is whether *ours* is running, not whether any broker is.
        A system broker on 1883 is someone else's — Studio can neither stop it
        nor claim it, so it must not suppress the button that starts ours.
        Conflating the two left the bar empty with no way forward.
        """
        if not self.broker().installed:
            return []

        if not commands.is_ours():
            return [ActionSpec("Start Broker", primary=True, on_click=self.start_broker)]

        actions = [
            ActionSpec("Stop Broker", on_click=self.stop_broker),
            ActionSpec("Restart Broker", on_click=self.restart_broker),
            Separator(),
            ActionSpec("Add Account", primary=True, on_click=self.add_account),
        ]

        # These need a row selected. They stay visible either way so the
        # toolbar does not change shape as you click around the list.
        selected = bool(self.selected_account)
        actions += [
            ActionSpec(
                "Reset Password",
                on_click=self.reset_password if selected else None,
            ),
            ActionSpec(
                "Remove Account",
                on_click=self.remove_account if selected else None,
            ),
        ]
        return actions

    def build_side(self) -> QWidget:
        """The account list. One row per thing that may join this network."""
        entries = self.accounts() if commands.is_ours() else []
        panel = SidePanel(
            "Accounts",
            [account.name for account in entries],
            enabled=bool(entries),
        )
        panel.currentTextChanged.connect(self._select_account)
        return panel

    def _select_account(self, name: str) -> None:
        self.selected_account = name
        # The actions depend on the selection, so the bar has to catch up.
        if self.on_rebuilt is not None:
            self.on_rebuilt()

    # ---- the sections, in order -----------------------------------------
    def build_page(self) -> QWidget:
        sections = [broker_card(self.broker(), self.last_result)]
        if commands.is_ours():
            sections.append(accounts_card(self.accounts()))
        return Column(*sections)

    # ---- broker actions --------------------------------------------------
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
        self.refresh_all()

    # ---- account actions -------------------------------------------------
    def add_account(self) -> None:
        parent = self.widget()
        name, full_access, confirmed = ask_for_name(parent)
        if not confirmed:
            return

        created, problem = account_service.add(name, full_access=full_access)
        if problem:
            QMessageBox.warning(parent, "Could not add the account", problem)
            return

        self.selected_account = created.account.name
        self.refresh_all()
        self._show_credentials(created.account.name, created.password, parent)

    def reset_password(self) -> None:
        parent = self.widget()
        name = self.selected_account
        if not name:
            return

        password, problem = account_service.reset_password(name)
        if problem:
            QMessageBox.warning(parent, "Could not reset the password", problem)
            return

        self._show_credentials(name, password, parent)

    def remove_account(self) -> None:
        parent = self.widget()
        name = self.selected_account
        if not name:
            return

        # Deleting a credential cannot be undone — the hash is gone and every
        # device using it stops connecting — so it is worth one question.
        confirm = QMessageBox.question(
            parent,
            f"Remove {name}?",
            f"{name} will no longer be able to connect, and anything already "
            "using its password will stop working. This cannot be undone.",
        )
        if confirm != QMessageBox.Yes:
            return

        problem = account_service.remove(name)
        if problem:
            QMessageBox.warning(parent, "Could not remove the account", problem)
            return

        self.selected_account = ""
        self.refresh_all()

    def _show_credentials(self, name: str, password: str, parent) -> None:
        CredentialsDialog(
            name,
            password,
            commands.DEFAULT_HOST,
            self.broker().port or commands.DEFAULT_PORT,
            parent,
        ).exec()
