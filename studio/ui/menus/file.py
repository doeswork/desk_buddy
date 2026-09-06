"""File menu — workflows, preferences, quit."""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMenu

from .base import SEPARATOR, Menu


class FileMenu(Menu):
    title = "File"

    entries = [
        ("New Workflow", "Ctrl+N"),
        ("Open…", "Ctrl+O"),
        ("Save", "Ctrl+S"),
        SEPARATOR,
    ]

    def build(self, window: QMainWindow, menu: QMenu) -> None:
        super().build(window, menu)

        preferences_action = QAction("Preferences…", window)
        preferences_action.triggered.connect(window.open_preferences)
        menu.addAction(preferences_action)
        window.preferences_action = preferences_action
        menu.addSeparator()

        restart_action = QAction("Restart App…", window)
        restart_action.triggered.connect(window.restart_app)
        menu.addAction(restart_action)
        window.restart_action = restart_action

        quit_action = QAction("Quit", window)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(window.close)
        menu.addAction(quit_action)
