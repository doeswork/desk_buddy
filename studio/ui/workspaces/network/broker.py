"""The Broker page: is Mosquitto here, and is it running?

View only. Every question it asks is answered by `studio.services.network` —
this file picks which card to build and nothing else. If a decision shows up
here that is about *brokers* rather than about *widgets*, it belongs in the
service.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QRadioButton, QWidget

from ....services.network import BrokerReport, CommandResult, lan_address
from ...components import Card, CommandCard, Column
from ...pages.base import Page
from ...theme.metrics import CARD_SPACING


class BrokerPage(Page):
    key = "broker"
    label = "Broker"

    title = "Broker"
    subtitle = "The MQTT hub. Robot, models, and workflows all talk through it."

    def build_page(self) -> QWidget:
        return Column(
            broker_card(self.workspace.broker(), self.workspace.last_result),
            BindHostCard(
                self.workspace.bind_lan,
                running=self.workspace.running(),
                on_change=self.workspace.set_bind_lan,
            ),
        )


def broker_card(report: BrokerReport, result: CommandResult | None = None) -> QWidget:
    """One card, chosen by whether the user still has something to install.

    `result` is the last start/stop attempt, when there was one. A failure
    outranks the status: "Port 18830 is already in use" is what the user needs
    to read, not "Mosquitto 2.1.2".
    """
    if result is not None and not result.ok:
        return Card(result.message, result.detail)

    if report.install_command:
        return CommandCard(report.headline, report.detail, report.install_command)

    return Card(report.headline, report.detail)


class BindHostCard(Card):
    """Localhost-only vs this network — off by default, and deliberate.

    Reaching a real robot needs the broker on the LAN; reaching only what is
    on this machine is the safer default (see DEFAULT_HOST's own reasoning
    in services/network/broker/commands.py). Disabled while running because
    the choice only takes effect on the next start — flipping it live would
    look like it changed something about a broker that is, in fact, still
    listening exactly where it always was.
    """

    def __init__(self, bind_lan: bool, *, running: bool, on_change) -> None:
        address = lan_address()
        super().__init__(
            "Who can reach this broker",
            "Takes effect on the next start or restart." if not running else
            "Stop or restart the broker to apply a change.",
        )

        layout = self.layout()
        layout.addSpacing(CARD_SPACING * 2)

        self._group = QButtonGroup(self)
        local = QRadioButton("This machine only (127.0.0.1)")
        lan = QRadioButton(
            f"This network ({address})" if address else
            "This network (no address found)"
        )
        lan.setEnabled(bool(address))
        for index, button in enumerate((local, lan)):
            button.setCursor(Qt.PointingHandCursor)
            button.setEnabled(button.isEnabled() and not running)
            self._group.addButton(button, index)
            layout.addWidget(button)
        (lan if bind_lan else local).setChecked(True)
        self._group.idToggled.connect(
            lambda index, checked: on_change(index == 1) if checked else None
        )
