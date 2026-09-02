"""View menu — appearance, zoom, and panel visibility.

The first menu that actually does something. Zoom scales the whole UI by
restyling the app font, which Qt propagates to every widget; the theme entries
swap the palette the stylesheet is rendered against.
"""

from __future__ import annotations

from functools import partial

from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMainWindow, QMenu

from .base import SEPARATOR, Menu
from ..theme import available

# Steps the UI scales through. 1.0 is the design size.
ZOOM_LEVELS = (0.75, 0.85, 1.0, 1.15, 1.3, 1.5, 1.75, 2.0)
DEFAULT_ZOOM_INDEX = ZOOM_LEVELS.index(1.0)


class ViewMenu(Menu):
    title = "View"

    def build(self, window: QMainWindow, menu: QMenu) -> None:
        self._build_theme(window, menu)
        menu.addSeparator()

        zoom_in = QAction("Zoom In", window)
        zoom_in.setShortcut("Ctrl++")
        zoom_in.triggered.connect(window.zoom_in)

        zoom_out = QAction("Zoom Out", window)
        zoom_out.setShortcut("Ctrl+-")
        zoom_out.triggered.connect(window.zoom_out)

        zoom_reset = QAction("Actual Size", window)
        zoom_reset.setShortcut("Ctrl+0")
        zoom_reset.triggered.connect(window.zoom_reset)

        menu.addAction(zoom_in)
        menu.addAction(zoom_out)
        menu.addAction(zoom_reset)
        menu.addSeparator()

        # Ctrl+= is what you get pressing + without shift on most keyboards.
        extra = QAction("Zoom In", window)
        extra.setShortcut("Ctrl+=")
        extra.triggered.connect(window.zoom_in)
        extra.setVisible(False)
        window.addAction(extra)

        # Panel toggles are appended by the window once docks exist.
        self.panel_section = menu

    def _build_theme(self, window: QMainWindow, menu: QMenu) -> None:
        """One exclusive, checkable entry per palette.

        build_menu_bar keeps the QMenu, not this instance, so the actions live
        on the window — that is also where set_theme reaches for them.
        """
        window.theme_actions = {}
        group = QActionGroup(window)
        group.setExclusive(True)

        for name, palette in available().items():
            action = QAction(palette.label, window)
            action.setCheckable(True)
            action.setChecked(name == window.theme)
            action.triggered.connect(partial(window.set_theme, name))
            group.addAction(action)
            menu.addAction(action)
            window.theme_actions[name] = action
