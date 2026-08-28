"""The Qt menu bar. Thin, quiet, holds what a toolbar shouldn't.

Everything is disabled except Quit. Add real behavior by connecting the
QActions this returns.
"""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMenu

SEPARATOR = "---"

# (menu title, [(label, shortcut), ...])  — SEPARATOR inserts a divider.
MENUS: list[tuple[str, list]] = [
    ("File", [
        ("New Workflow", "Ctrl+N"),
        ("Open…", "Ctrl+O"),
        ("Save", "Ctrl+S"),
        SEPARATOR,
        ("Preferences…", ""),
        SEPARATOR,
        ("Quit", "Ctrl+Q"),
    ]),
    ("Edit", [
        ("Undo", "Ctrl+Z"),
        ("Redo", "Ctrl+Shift+Z"),
        SEPARATOR,
        ("Cut", "Ctrl+X"),
        ("Copy", "Ctrl+C"),
        ("Paste", "Ctrl+V"),
    ]),
    ("Robot", [
        ("Connect…", ""),
        ("Disconnect", ""),
        SEPARATOR,
        ("Reboot", ""),
        ("Firmware Update…", ""),
    ]),
    ("View", []),   # dock toggles are appended here at runtime
    ("Help", [
        ("Build Guide", ""),
        ("MQTT Spec", ""),
        SEPARATOR,
        ("About Desk Buddy Studio", ""),
    ]),
]


def build_menu_bar(window: QMainWindow) -> QMenu:
    """Build every menu. Returns the View menu so docks can add toggles."""
    bar = window.menuBar()
    view_menu = None

    for title, entries in MENUS:
        menu = bar.addMenu(title)
        if title == "View":
            view_menu = menu

        for entry in entries:
            if entry == SEPARATOR:
                menu.addSeparator()
                continue

            label, shortcut = entry
            action = QAction(label, window)
            if shortcut:
                action.setShortcut(shortcut)

            if label == "Quit":
                action.triggered.connect(window.close)
            else:
                action.setEnabled(False)  # nothing else is wired up yet

            menu.addAction(action)

    return view_menu
