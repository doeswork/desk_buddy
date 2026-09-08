"""Broker setup progress and a collapsed, lazy manual fallback.

The workspace owns the setup lifecycle. Building this view never starts a
process or creates an account, including when built as an invisible page.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ....services.network import (
    firewall_allows,
    firewall_hint,
)
from ....services.network.broker import system
from ...components import Card, CommandCard, Column, StepsCard
from ...pages.base import Page
from ...theme.metrics import CARD_MARGIN_H, CARD_SPACING


class BrokerPage(Page):
    key = "broker"
    label = "Broker"
    title = "Broker"
    subtitle = "The MQTT hub. Robot, models, and workflows all talk through it."

    def enter(self) -> None:
        # Workspace activation owns setup; page navigation only repaints state.
        self.rebuild()

    def build_page(self) -> QWidget:
        state = self.workspace.setup.status
        titles = {
            "idle": "Broker setup", "checking": "Connecting…",
            "installing": "Installing Mosquitto…", "configuring": "Preparing your broker…",
            "starting": "Starting the broker…", "verifying": "Checking the connection…",
            "ready": "Studio is connected", "failed": "Setup needs attention",
            "cancelled": "Setup paused",
        }
        card = Card(titles[state.state], state.message)
        if (
            state.connected
            and state.network_ready
            and not self.workspace.wsl_robot_access_available()
        ):
            network = QLabel(
                f"Robot address: {state.robot_host}:{system.port()}. "
                "Network configured; robot connection not yet confirmed."
            )
            network.setObjectName("CardBody")
            network.setWordWrap(True)
            card.layout().addWidget(network)
        if state.state in ("failed", "cancelled"):
            # Human-facing reason stays visible; command output lives below.
            if state.detail:
                reason = QLabel(state.detail)
                reason.setObjectName("FieldError")
                reason.setWordWrap(True)
                card.layout().addWidget(reason)
            retry = QPushButton("Retry setup")
            retry.setObjectName("ToolbarPrimary")
            retry.clicked.connect(lambda: self.workspace.start_setup(retry=True))
            card.layout().addWidget(retry)
        sections = [card]
        account_reload = self._account_reload()
        if account_reload is not None:
            sections.append(account_reload)
        robot_access = self._wsl_robot_access()
        if robot_access is not None:
            sections.append(robot_access)
        sections.append(self._advanced())
        return Column(*sections)

    def _account_reload(self) -> QWidget | None:
        """Pending account files, separate from the connected broker result."""
        pending = self.workspace.account_reload_pending()
        state = self.workspace.account_reload.status
        if self.workspace.account_reload.running:
            return Card("Applying broker user changes", state.message)
        if not pending:
            return None

        card = Card(
            "Broker user changes need attention",
            "The user files are saved, but Mosquitto has not reloaded them yet.",
        )
        detail = state.detail or self.workspace.account_problem
        if detail:
            reason = QLabel(detail)
            reason.setObjectName("FieldError")
            reason.setWordWrap(True)
            card.layout().addWidget(reason)
        retry = QPushButton("Retry applying changes")
        retry.setObjectName("ToolbarPrimary")
        retry.setEnabled(not self.workspace.setup.running)
        retry.clicked.connect(
            lambda: self.workspace.start_account_reload(retry=True)
        )
        card.layout().addWidget(retry)
        return card

    def _wsl_robot_access(self) -> QWidget | None:
        """The opt-in Windows boundary, separate from local broker health."""
        broker = self.workspace.setup.status
        if (
            not self.workspace.wsl_robot_access_available()
            or not broker.connected
            or broker.state != "ready"
        ):
            return None

        access = self.workspace.robot_access.status
        titles = {
            "idle": "Connect robots on your network",
            "checking": "Checking robot access…",
            "disabled": "Connect robots on your network",
            "repair": "Repair robot access",
            "enabling": "Enabling robot access…",
            "disabling": "Disabling robot access…",
            "ready": "Robot access is ready",
            "failed": "Robot access needs attention",
            "cancelled": (
                "Robot access is still enabled"
                if access.ready
                else "Robot access was not changed"
            ),
        }
        message = access.message
        if access.state in ("idle", "disabled"):
            message = (
                "Studio can use MQTT inside WSL. Allow Windows to forward the "
                "broker port before connecting a robot on your home network."
            )
        card = Card(titles.get(access.state, "Connect robots on your network"), message)

        if access.ready and access.host and not access.message.startswith("Robot address:"):
            address = QLabel(f"Robot address: {access.host}:{system.port()}")
            address.setObjectName("CardBody")
            address.setWordWrap(True)
            card.layout().addWidget(address)
        if access.detail:
            detail = QLabel(access.detail)
            detail.setObjectName(
                "FieldError" if access.state in ("failed", "cancelled") else "CardBody"
            )
            detail.setWordWrap(True)
            card.layout().addWidget(detail)

        if self.workspace.robot_access.running:
            return card

        if access.ready:
            disable = QPushButton("Disable robot access")
            disable.setObjectName("ToolbarAction")
            disable.clicked.connect(
                lambda: self.workspace.start_robot_access("disable")
            )
            card.layout().addWidget(disable)
            return card

        labels = {
            "repair": "Repair robot access",
            "failed": "Retry robot access",
        }
        retrying_disable = access.action == "disable"
        label = (
            "Retry disabling robot access"
            if retrying_disable
            else labels.get(access.state, "Enable robot access")
        )
        enable = QPushButton(label)
        enable.setObjectName("ToolbarPrimary")
        enable.clicked.connect(
            lambda: self.workspace.start_robot_access(
                "disable" if retrying_disable else "enable"
            )
        )
        card.layout().addWidget(enable)
        return card

    def _advanced(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        toggle = QPushButton("Advanced / Manual setup")
        toggle.setObjectName("ToolbarAction")
        toggle.setCheckable(True)
        toggle.setChecked(self.workspace.advanced_open)
        layout.addWidget(toggle)
        content = QWidget()
        content.setObjectName("ManualSetupContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(content)
        content.setVisible(self.workspace.advanced_open)

        def populate():
            if content_layout.count():
                return
            # Lazy construction prevents rendering a hidden page from generating
            # credentials, querying firewalls, or running any setup operation.
            from ....storage.settings import settings
            from ....storage import keys
            store = settings()
            connection = Card("Connection settings", "Leave the host blank to set up this machine. Enter an existing broker account to connect to a custom broker.")
            fields = QFormLayout()
            host = QLineEdit(store.get(keys.SYSTEM_BROKER_HOST))
            port = QSpinBox()
            port.setRange(1, 65535)
            port.setValue(system.port(store))
            user = QLineEdit(store.get(keys.SYSTEM_BROKER_USER))
            secret = QLineEdit(store.get(keys.SYSTEM_BROKER_PASSWORD))
            secret.setEchoMode(QLineEdit.Password)
            for label, field in (("Broker host", host), ("Port", port), ("Username", user), ("Password", secret)):
                fields.addRow(label, field)
            connection.layout().addLayout(fields)
            save = QPushButton("Save and retry")
            save.setEnabled(not self.workspace.setup.running)

            def save_connection():
                if self.workspace.setup.running:
                    return
                system.set_connection(host_value=host.text(), port_value=port.value(),
                                      user=user.text(), password=secret.text(), backend=store)
                self.workspace.start_setup(retry=True)

            save.clicked.connect(save_connection)
            connection.layout().addWidget(save)
            content_layout.addWidget(connection)
            details = Card("Diagnostics", self.workspace.setup.status.detail or self.workspace.setup.status.message)
            content_layout.addWidget(details)
            if (
                self.workspace.setup.status.connected
                and not self.workspace.setup.running
                and self.workspace.write_access().allowed
            ):
                content_layout.addWidget(ManagedNote(
                    "Studio manages this broker’s users.",
                    on_revoke=self._revoke,
                    problem=self.workspace.grant_problem,
                ))
            content_layout.addWidget(StepsCard("Install and start Mosquitto", "Manual fallback for this operating system.", system.install_instructions()))
            name = store.get(keys.SYSTEM_BROKER_USER) or "studio"
            password = store.get(keys.SYSTEM_BROKER_PASSWORD) or "<generated during setup>"
            content_layout.addWidget(StepsCard("Studio account", "These commands contain the broker password. Keep it private.", system.account_instructions(name, password)))
            content_layout.addWidget(StepsCard("Broker configuration", "Review existing listeners and authentication before applying these fallback commands. Automatic setup checks compatibility for you.", system.listener_instructions(system.port())))
            content_layout.addWidget(StepsCard("Account management", "Grant Studio permission to manage broker accounts.", system.grant_instructions()))
            firewall = firewall_card(system.port())
            if firewall is not None:
                content_layout.addWidget(firewall)

        def expanded(checked):
            self.workspace.advanced_open = checked
            if checked:
                populate()
            content.setVisible(checked)

        toggle.toggled.connect(expanded)
        if self.workspace.advanced_open:
            populate()
        return panel

    def _revoke(self) -> None:
        self.workspace.revoke_access()


def firewall_card(port: int) -> QWidget | None:
    """Say whether the firewall lets robots in, and only ask when unsure.

    This used to warn whenever any firewall was up, because reading the
    rules was assumed to need root. `ufw status` does — but the file it
    reads is world-readable, so the question is answerable after all, and
    `firewall_allows()` answers it.

    That collapses three cases into their honest shapes:

        allowed     nothing to say, so no card at all
        blocked     a real problem, named, with the command that fixes it
        unknown     firewalld, or unreadable rules — the old warning, which
                    is still right when Studio genuinely cannot tell

    A card that appears next to a working setup teaches the user to skip
    cards, which costs more than the one it was trying to save them.
    """
    verdict = firewall_allows(port)
    if not verdict.firewall:
        return None

    if verdict.allowed:
        # Read the rules and found the port open: nothing to warn about.
        return None

    if verdict.blocked:
        return CommandCard(
            f"{verdict.firewall} is blocking port {port}",
            f"The broker is listening, but {verdict.firewall} has no rule "
            f"letting anything on the network reach port {port} — so a robot "
            "is refused before it arrives, while Studio's own connection "
            "over loopback succeeds. This is the rule that opens it:",
            firewall_hint(port, verdict.firewall),
        )

    return CommandCard(
        f"{verdict.firewall} is running on this machine",
        f"If a robot cannot reach the broker on port {port}, the firewall is "
        "the first thing to check — a listening broker is still refused "
        "until the port is opened. Studio cannot read "
        f"{verdict.firewall}'s rules, so this may already be allowed:",
        firewall_hint(port, verdict.firewall),
    )


class ManagedNote(QWidget):
    """One line saying Studio manages the users, with a quiet way to undo it.

    Not a Card. A card is for something the user has to read or act on, and
    a permission that is already granted is neither — it is a fact worth
    confirming in passing. Given a card, it competed with the setup steps
    around it for attention it did not deserve, and did it on a page the
    user only visits when something needs doing.

    Revoke is a text link rather than a button for the same reason: it is a
    rare action with real consequences, and the shape it should have is
    "available if you go looking", not "one of the things to do here".
    """

    def __init__(self, text: str, *, on_revoke=None, problem: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(CARD_MARGIN_H, 0, CARD_MARGIN_H, 0)
        layout.setSpacing(CARD_SPACING)

        row = QHBoxLayout()
        row.setSpacing(CARD_SPACING * 2)

        label = QLabel(text)
        label.setObjectName("CardBody")
        label.setWordWrap(True)
        row.addWidget(label)

        if on_revoke is not None:
            revoke = QLabel("Revoke")
            revoke.setObjectName("RowAction")
            revoke.setCursor(Qt.PointingHandCursor)
            revoke.setToolTip(
                "Hand the broker's account files back to root. The users "
                "themselves are not changed, but Studio can no longer edit "
                "them, and granting again means running the setup once more."
            )
            revoke.mousePressEvent = lambda _event: on_revoke()
            row.addWidget(revoke)

        row.addStretch(1)
        layout.addLayout(row)

        if problem:
            note = QLabel(problem)
            note.setObjectName("FieldError")
            note.setWordWrap(True)
            layout.addWidget(note)
