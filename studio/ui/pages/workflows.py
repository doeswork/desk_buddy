"""Workflows — node graph and text editor over the same YAML."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

try:
    from ..widgets import card
    from .base import SEPARATOR, Page, stack
except ImportError:
    from widgets import card
    from base import SEPARATOR, Page, stack


class WorkflowsPage(Page):
    key = "workflows"
    label = "Workflows"

    title = "Workflow Manager"
    subtitle = "Node graph and text editor over the same YAML."
    status = "No workflow loaded"

    actions = [
        "New", "Open", "Save", SEPARATOR,
        "Run", "Stop", SEPARATOR,
        "Graph", "Text",
    ]

    side_title = "Workflows"
    side_items = ["wave_hello.yaml", "find_and_grab.yaml", "desk_patrol.yaml"]

    def build_page(self) -> QWidget:
        return stack(
            card("No workflow open", "Create one, or open an example."),
            card("Last run", "Never run."),
        )
