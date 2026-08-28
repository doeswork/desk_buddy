"""File menu — workflows, preferences, quit."""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMenu

try:
    from .base import SEPARATOR, Menu
except ImportError:
    from base import SEPARATOR, Menu


class FileMenu(Menu):
    title = "File"

    entries = [
        ("New Workflow", "Ctrl+N"),
        ("Open…", "Ctrl+O"),
        ("Save", "Ctrl+S"),
        SEPARATOR,
        ("Preferences…", ""),
    ]

    def build(self, window: QMainWindow, menu: QMenu) -> None:
        super().build(window, menu)
        menu.addSeparator()

        # Quit is the one thing in this menu that works.
        quit_action = QAction("Quit", window)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(window.close)
        menu.addAction(quit_action)
