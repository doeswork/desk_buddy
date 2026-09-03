"""Workflows — node graph and text editor over the same YAML."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ..components import ActionSpec, Card, Column, Separator
from ..pages.base import Page
from .base import Workspace


class EditorPage(Page):
    key = "editor"
    label = "Editor"

    title = "Workflow Manager"
    subtitle = "Node graph and text editor over the same YAML."
    status = "No workflow loaded"

    def build_page(self) -> QWidget:
        return Column(
            self.build_editor(),
            self.build_last_run(),
        )

    def build_editor(self) -> QWidget:
        return Card("No workflow open", "Create one, or open an example.")

    def build_last_run(self) -> QWidget:
        return Card("Last run", "Never run.")


class WorkflowsWorkspace(Workspace):
    key = "workflows"
    label = "Workflows"
    page_classes = [EditorPage]

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
