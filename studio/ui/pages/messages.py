"""Messages — the MQTT bus. Everything on the stack talks through it."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

try:
    from ..widgets import card
    from .base import SEPARATOR, Page, stack
except ImportError:
    from widgets import card
    from base import SEPARATOR, Page, stack


class MessagesPage(Page):
    key = "messages"
    label = "Messages"

    title = "Messages"
    subtitle = "The MQTT bus. Everything on the stack talks through it."
    status = "Broker stopped"

    actions = ["Start", "Stop", "Restart", SEPARATOR, "Config", "Topics"]

    side_title = "Brokers"
    side_items = ["Local broker", "mqtt.deskbuddy.ai", "Custom…"]

    def build_page(self) -> QWidget:
        return stack(
            card("Broker", "Not running. No broker configured yet."),
            card("Connected clients", "Nothing connected."),
            card("Traffic", "No messages seen."),
        )
