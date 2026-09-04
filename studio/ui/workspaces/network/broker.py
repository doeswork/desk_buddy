"""The Broker page: is Mosquitto here, and is it running?

View only. Every question it asks is answered by `studio.services.network` —
this file picks which card to build and nothing else. If a decision shows up
here that is about *brokers* rather than about *widgets*, it belongs in the
service.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ....services.network import (
    BrokerReport,
    CommandResult,
    active_firewall,
    firewall_hint,
    lan_address,
    our_port,
)
from ....services.network.broker.finder import DEFAULT_PORT
from ...components import Card, CommandCard, Column
from ...pages.base import Page


class BrokerPage(Page):
    key = "broker"
    label = "Broker"

    title = "Broker"
    subtitle = "The MQTT hub. Robot, models, and workflows all talk through it."

    def build_page(self) -> QWidget:
        sections = [
            broker_card(self.workspace.broker(), self.workspace.last_result),
            address_card(self.workspace.broker_host()),
        ]
        # Studio's own port, not broker().port — that reports whichever
        # broker was detected, which may be an unrelated system one on 1883.
        firewall = firewall_card(our_port() or DEFAULT_PORT)
        if firewall is not None:
            sections.append(firewall)
        return Column(*sections)


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


def firewall_card(port: int) -> QWidget | None:
    """Warn that a firewall is running, with the command that opens the port.

    Deliberately shown whenever a firewall is up rather than only when the
    port is known-blocked: reading the rules needs root, and a connect from
    this machine goes over loopback and succeeds even while an external
    device is refused. So Studio cannot tell the two apart — and a broker
    that is listening but unreachable looks exactly like a broken robot,
    which is worth one card to rule out.
    """
    firewall = active_firewall()
    if not firewall:
        return None
    return CommandCard(
        f"{firewall} is running on this machine",
        f"If a robot cannot reach the broker on port {port}, the firewall is "
        "the first thing to check — a listening broker is still refused "
        "until the port is opened. Studio cannot read the rules (that needs "
        "root), so this may already be allowed:",
        firewall_hint(port, firewall),
    )


def address_card(bound_host: str) -> QWidget:
    """Where a robot reaches this broker.

    The broker always binds to this machine's LAN address — a robot is
    always a separate device, so there is no second case to offer. This
    only reports the address, since it is what has to be typed into (or
    provisioned onto) a robot.
    """
    if bound_host:
        return Card(
            "Robots reach this broker at",
            f"{bound_host} — provisioned automatically by Set MQTT on the "
            "Serial Monitor.",
        )

    address = lan_address()
    if not address:
        return Card(
            "No network address",
            "This machine is not on a network, so a robot has no way to "
            "reach the broker. Connect it to the same network as the robot.",
        )
    return Card(
        "Robots will reach this broker at",
        f"{address}, once it is started.",
    )
