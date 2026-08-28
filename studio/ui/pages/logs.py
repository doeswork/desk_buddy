"""Logs — every service, the bus, and workflow runs, in one place."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

try:
    from ..widgets import card
    from .base import SEPARATOR, Page, stack
except ImportError:
    from widgets import card
    from base import SEPARATOR, Page, stack


class LogsPage(Page):
    key = "logs"
    label = "Logs"

    title = "Logs"
    subtitle = "Every service, the bus, and workflow runs, in one place."
    status = "0 lines"

    actions = ["Pause", "Clear", SEPARATOR, "Copy", "Export", "Filter…"]

    side_title = "Sources"
    side_items = ["All", "Broker", "Vision", "Workflows", "Robot", "Studio"]

    def build_page(self) -> QWidget:
        return stack(
            card("No log output", "Services write here once they are running."),
        )
