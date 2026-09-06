"""Help menu — docs, about, and diagnostics."""

from __future__ import annotations

from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import QMainWindow, QMenu

from .base import SEPARATOR, Menu


class HelpMenu(Menu):
    title = "Help"

    entries = [
        ("Build Guide", ""),
        ("MQTT Spec", ""),
        SEPARATOR,
        ("About Desk Buddy Studio", ""),
    ]

    def build(self, window: QMainWindow, menu: QMenu) -> None:
        super().build(window, menu)
        menu.addSeparator()

        # The one entry here that works. Puts everything needed to explain a
        # misbehaving app on the clipboard, so a bug report can be pasted
        # rather than described.
        copy = QAction("Copy Diagnostics", window)
        copy.setShortcut("Ctrl+Shift+D")
        copy.triggered.connect(lambda: self.copy_diagnostics(window))
        menu.addAction(copy)

    @staticmethod
    def copy_diagnostics(window: QMainWindow) -> None:
        from ...diagnostics import text

        report = text(window)
        QGuiApplication.clipboard().setText(report)
        window.statusBar().showMessage(
            f"Diagnostics copied — {len(report.splitlines())} lines", 3000
        )
