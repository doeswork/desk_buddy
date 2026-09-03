"""The Broker page: is Mosquitto here, and is it running?

View only. Every question it asks is answered by `studio.services.network` —
this file picks which card to build and nothing else. If a decision shows up
here that is about *brokers* rather than about *widgets*, it belongs in the
service.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ....services.network import BrokerReport, CommandResult
from ...components import Card, CommandCard, Column
from ...pages.base import Page


class BrokerPage(Page):
    key = "broker"
    label = "Broker"

    title = "Broker"
    subtitle = "The MQTT hub. Robot, models, and workflows all talk through it."

    def build_page(self) -> QWidget:
        return Column(
            broker_card(self.workspace.broker(), self.workspace.last_result)
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
