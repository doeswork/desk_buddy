"""BAR 2 — the context bar.

Holds only the actions for the page you are on, and is rebuilt from scratch on
every page change. That swap is the core of the FreeCAD model: pick a page, the
whole toolset below it changes.

A page declares its actions as plain strings (see pages/base.py). One may be
marked primary — it renders filled; the rest render outlined.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow, QPushButton, QToolBar

try:
    from .action_spec import Separator
    from .spacer import spacer
except ImportError:
    from action_spec import Separator
    from spacer import spacer


class ContextButton(QPushButton):
    """One action on the context bar.

    `primary=True` gives the filled treatment — at most one per page, or none.
    Styling lives in theme.py under QPushButton#ContextPrimary / #ContextAction.
    """

    def __init__(self, text: str, *, primary: bool = False, enabled: bool = False) -> None:
        super().__init__(text)
        self.setObjectName("ContextPrimary" if primary else "ContextAction")
        self.setCursor(Qt.PointingHandCursor)
        self.setEnabled(enabled)
        self.setFlat(True)


class ContextBar(QToolBar):
    def __init__(self, window: QMainWindow) -> None:
        super().__init__("Actions", window)
        self.setObjectName("ContextBar")
        self.setMovable(False)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._window = window
        self.buttons: dict[str, ContextButton] = {}

    def show_page(self, page) -> None:
        """Rebuild the bar for one page."""
        self.clear()
        self.buttons = {}

        for spec in page.build_actions():
            if isinstance(spec, Separator):
                self.addSeparator()
                continue

            button = ContextButton(
                spec.label,
                primary=spec.primary,
                enabled=spec.enabled,
            )
            self.addWidget(button)
            self.buttons[spec.label] = button

        self.addWidget(spacer())
        self.addWidget(QLabel(f"{page.status}   "))
