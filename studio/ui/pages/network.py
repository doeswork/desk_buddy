"""Network — the communication hub. Everything on the stack talks through it."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

try:
    from ..components import ActionSpec, Card, Column, Separator, SidePanel
    from .base import Page
except ImportError:
    from components import ActionSpec, Card, Column, Separator, SidePanel
    from base import Page


class NetworkPage(Page):
    key = "network"
    label = "Network"

    title = "Network"
    subtitle = "The MQTT hub. Robot, models, and workflows all talk through it."
    status = "Broker stopped"

    def build_actions(self) -> list:
        return [
            ActionSpec("Start", primary=True),
            ActionSpec("Stop"),
            ActionSpec("Restart"),
            Separator(),
            ActionSpec("Config"),
            ActionSpec("Topics"),
        ]

    def build_side(self) -> QWidget:
        return SidePanel("Brokers", ["Local broker", "mqtt.deskbuddy.ai", "Custom…"])

    def build_page(self) -> QWidget:
        return Column(
            self.build_broker(),
            self.build_clients(),
            self.build_traffic(),
        )

    def build_broker(self) -> QWidget:
        return Card("Broker", "Not running. No broker configured yet.")

    def build_clients(self) -> QWidget:
        return Card("Connected", "Nothing connected.")

    def build_traffic(self) -> QWidget:
        return Card("Traffic", "No messages seen.")
