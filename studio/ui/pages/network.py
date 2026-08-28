"""Network — the communication hub. Everything on the stack talks through it."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

try:
    from ..widgets import card
    from .base import SEPARATOR, Page, stack
except ImportError:
    from widgets import card
    from base import SEPARATOR, Page, stack


class NetworkPage(Page):
    key = "network"
    label = "Network"

    title = "Network"
    subtitle = "The MQTT hub. Robot, models, and workflows all talk through it."
    status = "Broker stopped"

    actions = ["Start", "Stop", "Restart", SEPARATOR, "Config", "Topics"]

    side_title = "Brokers"
    side_items = ["Local broker", "mqtt.deskbuddy.ai", "Custom…"]

    def build_page(self) -> QWidget:
        return stack(
            card("Broker", "Not running. No broker configured yet."),
            card("Connected", "Nothing connected."),
            card("Traffic", "No messages seen."),
        )
