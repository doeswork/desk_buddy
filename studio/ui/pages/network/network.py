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

from ....services.network import accounts as account_service
from ....services.network import broker_commands as commands
from ....services.network import report
from ....services.vision.access import (
    VisionAccessIdentity,
    VisionAccessManager,
    default_vision_access,
)
from ...components import ActionSpec, Column, Separator, SidePanel
from ..base import Page
from ..vision.access_widgets import AccessCallbacks, vision_access_card
from .accounts import CredentialsDialog, accounts_card, ask_for_name
from .broker import broker_card


class NetworkPage(Page):
    key = "network"
    label = "Network"

    title = "Network"
    subtitle = "The MQTT hub. Robot, models, and workflows all talk through it."
    built = True

    def __init__(self, *, vision_access: VisionAccessManager | None = None) -> None:
        super().__init__()
        self.vision_access = vision_access or default_vision_access()
        self.vision_access.changed.connect(self._vision_access_changed)
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
        managed = self.vision_access.is_managed_account(self.selected_account) if selected else False
        actions += [
            ActionSpec(
                "Reset Password",
                on_click=self.reset_password if selected and not managed else None,
            ),
            ActionSpec(
                "Remove Account",
                on_click=self.remove_account if selected and not managed else None,
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
        plan = self.vision_access.known_plan()
        errors = self.vision_access.last_result.errors if self.vision_access.last_result else ()
        detail = "Studio manages these localhost credentials. Passwords are stored privately and remain visible here."
        if errors:
            detail += "\n" + "\n".join(errors)
        callbacks = AccessCallbacks(
            setup_all=self.setup_vision_access,
            ensure=self.ensure_vision_access,
            rotate=self.rotate_vision_access,
            revoke=self.revoke_vision_access,
            copy_setup=self.copy_vision_setup,
        )
        access = vision_access_card(
            plan.identities,
            self.vision_access.states(plan),
            callbacks,
            setup_label="Set Up/Repair All" if commands.is_ours() else "Start Broker & Set Up Vision Access",
            setup_enabled=self.vision_access.can_manage_broker,
            detail=detail,
        )
        sections = [broker_card(self.broker(), self.last_result), access]
        if commands.is_ours():
            sections.append(accounts_card(self.accounts()))
        return Column(*sections)

    # ---- managed Vision access -----------------------------------------
    def setup_vision_access(self) -> None:
        if not self.vision_access.can_manage_broker:
            QMessageBox.information(
                self.widget(), "External MQTT broker",
                "Studio only provisions its own broker at 127.0.0.1:18830. "
                "Supply external broker credentials through environment variables.",
            )
            return
        if not commands.is_ours():
            self.last_result = commands.start()
            if not self.last_result:
                self.refresh_all()
                return
        result = self.vision_access.ensure_all()
        if not result.ok:
            QMessageBox.warning(
                self.widget(), "Some Vision accounts need attention", "\n".join(result.errors)
            )
        self.refresh_all()

    def ensure_vision_access(self, identity: VisionAccessIdentity) -> None:
        self._vision_access_action(
            "Could not create Vision access", lambda: self.vision_access.ensure(identity)
        )

    def rotate_vision_access(self, identity: VisionAccessIdentity) -> None:
        if QMessageBox.question(
            self.widget(), f"Rotate {identity.role} access?",
            "The current password will stop working immediately. Continue?",
        ) == QMessageBox.Yes:
            self._vision_access_action(
                "Could not rotate Vision access", lambda: self.vision_access.rotate(identity)
            )

    def revoke_vision_access(self, identity: VisionAccessIdentity) -> None:
        if QMessageBox.question(
            self.widget(), f"Revoke {identity.role} access?",
            "This service will be disconnected and cannot reconnect until access is created again.",
        ) == QMessageBox.Yes:
            self._vision_access_action(
                "Could not revoke Vision access", lambda: self.vision_access.revoke(identity)
            )

    def copy_vision_setup(self, identity: VisionAccessIdentity) -> None:
        from PySide6.QtGui import QGuiApplication

        try:
            QGuiApplication.clipboard().setText(self.vision_access.setup_bundle(identity))
            self.status = f"Copied MQTT setup for {identity.role}"
            if self.on_rebuilt is not None:
                self.on_rebuilt()
        except Exception as exc:
            QMessageBox.warning(self.widget(), "Could not copy Vision setup", str(exc))

    def _vision_access_action(self, title: str, callback) -> None:
        try:
            callback()
        except Exception as exc:
            QMessageBox.warning(self.widget(), title, str(exc))
        self.refresh_all()

    def _vision_access_changed(self) -> None:
        self._side = None
        self.refresh()

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
