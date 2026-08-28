"""View menu — zoom, and panel visibility.

The first menu that actually does something. Zoom scales the whole UI by
restyling the app font, which Qt propagates to every widget.
"""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMenu

try:
    from .base import SEPARATOR, Menu
except ImportError:
    from base import SEPARATOR, Menu

# Steps the UI scales through. 1.0 is the design size.
ZOOM_LEVELS = (0.75, 0.85, 1.0, 1.15, 1.3, 1.5, 1.75, 2.0)
DEFAULT_ZOOM_INDEX = ZOOM_LEVELS.index(1.0)


class ViewMenu(Menu):
    title = "View"

    def build(self, window: QMainWindow, menu: QMenu) -> None:
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
