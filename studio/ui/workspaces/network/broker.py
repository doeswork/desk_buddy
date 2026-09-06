"""The Broker page: is Mosquitto here, is it running, and may Studio use it?

View only. Every question it asks is answered by `studio.services.network` —
this file picks which card to build and nothing else. If a decision shows up
here that is about *brokers* rather than about *widgets*, it belongs in the
service.

The page is a sequence, not a dashboard: it shows the one step that is due
(install it, expose it to the LAN, give Studio an account, let Studio manage
accounts) rather than everything at once, because a setup page that shows
everything is a setup page nobody reads.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....services.network import (
    firewall_allows,
    firewall_hint,
    lan_address,
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
        """Re-read the two things the user changes outside Studio.

        This page is where the setup instructions live, which makes it the
        page they come back to after running them in a terminal — so a
        stale answer here reads as the app being broken. Both the file
        permissions and the connection are re-checked on the way in, which
        is what makes "run these, then reopen this page" a complete loop.
        """
        self.workspace.recheck_access()
        self.workspace.recheck_connection()

    def build_page(self) -> QWidget:
        broker = self.workspace.system_broker()
        sections: list[QWidget] = [Card(broker.headline, broker.detail)]

        if not broker.installed:
            sections.append(
                StepsCard(
                    "Install a broker",
                    "Studio uses the broker this machine runs rather than "
                    "starting one of its own.",
                    system.install_instructions(),
                )
            )
            return Column(*sections)

        if not broker.reachable:
            sections.append(
                StepsCard(
                    "Start the broker",
                    f"Nothing is answering on port {broker.port}.",
                    system.install_instructions()[1:]
                    + system.listener_instructions(broker.port),
                )
            )
            if not lan_address():
                # Worth saying here rather than once the broker is up: a
                # machine with no network cannot serve a robot however well
                # its broker is running.
                sections.append(
                    Card(
                        "No network address",
                        "This machine is not on a network, so a robot has no "
                        "way to reach the broker even once it starts. "
                        "Connect it to the same network as the robot.",
                    )
                )
            return Column(*sections)

        # No separate address card: the headline already names the address,
        # and its detail line already tells robots where to find it. Three
        # cards repeating one IP is how a page stops being read.

        if not broker.configured:
            sections.append(self._account_setup_card())

        sections.append(self._manage_card(self.workspace.write_access()))

        firewall = firewall_card(broker.port)
        if firewall is not None:
            sections.append(firewall)
        return Column(*sections)

    def _account_setup_card(self) -> QWidget:
        """Studio's own account: the commands that create it.

        No "check it worked" button. Studio tries the credentials whenever
        this page is built, so running the commands and coming back is the
        whole loop — the card is gone next time if they worked, and still
        here with the reason if they did not.
        """
        name, password = system.suggested_account()
        card = StepsCard(
            "Create Studio's account",
            "Studio needs an account on the broker to publish and subscribe. "
            "Run these, then reopen this page. The password below is the one "
            "Studio has recorded.",
            system.account_instructions(name, password),
        )

        if self.workspace.verify_problem:
            problem = QLabel(self.workspace.verify_problem)
            problem.setObjectName("FieldError")
            problem.setWordWrap(True)
            card.layout().addWidget(problem)
        return card

    def _manage_card(self, access) -> QWidget:
        """Whether Studio may manage the broker's users.

        Asymmetric on purpose, because the two states are not equally
        interesting. Not being able to manage users is a setup step that is
        not finished, and it gets the full treatment: what is wrong, the
        button that fixes it, and the exact commands underneath.

        Being able to is just a fact, and it gets one line with a quiet way
        back. The commands to undo it are not worth a permanent block of
        terminal text on a page whose setup is complete — that was the card
        shouting a finished step at someone who had already done it three
        times.
        """
        if access.allowed:
            return ManagedNote(
                "Studio manages this broker's users.",
                on_revoke=self._revoke if system.can_grant() else None,
                problem=self.workspace.grant_problem,
            )

        body = (
            f"{access.reason} Until then, Add User is unavailable and "
            "accounts have to be created with mosquitto_passwd."
        )
        body += (
            " Grant Access hands your desktop's password prompt the commands "
            "below — Studio never sees your password, and never runs sudo "
            "itself."
            if system.can_grant()
            else " Run these commands to grant it; they are needed once."
        )
        card = StepsCard(
            "Let Studio manage this broker's users",
            body,
            system.grant_instructions(),
        )
        if system.can_grant():
            grant = QPushButton("Grant Access")
            grant.setObjectName("ContextPrimary")
            grant.setCursor(Qt.PointingHandCursor)
            grant.clicked.connect(self._grant)
            card.layout().addWidget(grant)

        if self.workspace.grant_problem:
            problem = QLabel(self.workspace.grant_problem)
            problem.setObjectName("FieldError")
            problem.setWordWrap(True)
            card.layout().addWidget(problem)
        return card

    def _grant(self) -> None:
        self.workspace.grant_access()

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
