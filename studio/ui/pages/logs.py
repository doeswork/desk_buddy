"""Logs — every service, the bus, and workflow runs, in one place."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ..components import ActionSpec, Card, Column, Separator, SidePanel
from .base import Page


class LogsPage(Page):
    key = "logs"
    label = "Logs"

    title = "Logs"
    subtitle = "Every service, the bus, and workflow runs, in one place."
    status = "0 lines"

    def build_actions(self) -> list:
        return [
            ActionSpec("Pause"),
            ActionSpec("Clear"),
            Separator(),
            ActionSpec("Copy"),
            ActionSpec("Export"),
            ActionSpec("Filter…"),
        ]

    def build_side(self) -> QWidget:
        return SidePanel("Sources", [
            "All", "Broker", "Vision", "Workflows", "Robot", "Studio",
        ])

    def build_page(self) -> QWidget:
        return Column(self.build_output())

    def build_output(self) -> QWidget:
        return Card("No log output", "Services write here once they are running.")
