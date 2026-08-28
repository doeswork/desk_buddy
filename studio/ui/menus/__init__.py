"""The menu bar, one file per menu, in left-to-right order.

To add a menu: write the file, import it, add it to MENU_CLASSES.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow

from .base import SEPARATOR, Menu
from .edit import EditMenu
from .file import FileMenu
from .help import HelpMenu
from .robot import RobotMenu
from .view import ViewMenu

MENU_CLASSES = [FileMenu, EditMenu, RobotMenu, ViewMenu, HelpMenu]


def build_menu_bar(window: QMainWindow) -> dict:
    """Build every menu. Returns {title: QMenu} so callers can append later."""
    bar = window.menuBar()
    built = {}

    for cls in MENU_CLASSES:
        instance = cls()
        menu = bar.addMenu(instance.title)
        instance.build(window, menu)
        built[instance.title] = menu

    return built


__all__ = ["SEPARATOR", "Menu", "MENU_CLASSES", "build_menu_bar"]
