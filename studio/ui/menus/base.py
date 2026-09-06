"""Base class every menu inherits.

A Menu owns one top-level entry in the menu bar: its title and its actions.
Adding a menu means adding one file and one line in `menus/__init__.py`.

Subclasses either set `entries` for plain disabled placeholders, or override
`build()` when the menu does real work.
"""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMenu

SEPARATOR = "---"


class Menu:
    title: str = ""

    # (label, shortcut) tuples, or SEPARATOR for a divider.
    entries: list = []

    def build(self, window: QMainWindow, menu: QMenu) -> None:
        """Fill the menu. Default: disabled placeholders from `entries`."""
        for entry in self.entries:
            if entry == SEPARATOR:
                menu.addSeparator()
                continue
            label, shortcut = entry
            menu.addAction(self.placeholder(window, label, shortcut))

    @staticmethod
    def placeholder(window: QMainWindow, label: str, shortcut: str = "") -> QAction:
        """An action that shows what will exist, but does nothing yet."""
        action = QAction(label, window)
        if shortcut:
            action.setShortcut(shortcut)
        action.setEnabled(False)
        return action
