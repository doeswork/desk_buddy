"""Workflows — node graph and text editor over the same YAML."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ..components import ActionSpec, Card, Column, Separator, SidePanel
from .base import Page


class WorkflowsPage(Page):
    key = "workflows"
    label = "Workflows"

    title = "Workflow Manager"
    subtitle = "Node graph and text editor over the same YAML."
    status = "No workflow loaded"

    def build_actions(self) -> list:
        return [
            ActionSpec("New"),
            ActionSpec("Open"),
            ActionSpec("Save"),
            Separator(),
            ActionSpec("Run", primary=True),
            ActionSpec("Stop"),
            Separator(),
            ActionSpec("Graph"),
            ActionSpec("Text"),
        ]

    def build_side(self) -> QWidget:
        return SidePanel("Workflows", [
            "wave_hello.yaml",
            "find_and_grab.yaml",
            "desk_patrol.yaml",
        ])

    def build_page(self) -> QWidget:
        return Column(
            self.build_editor(),
            self.build_last_run(),
        )

    def build_editor(self) -> QWidget:
        return Card("No workflow open", "Create one, or open an example.")

    def build_last_run(self) -> QWidget:
        return Card("Last run", "Never run.")
