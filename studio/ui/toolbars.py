"""The two bars.

BAR 1  page switcher — built once, never changes
BAR 2  context bar — cleared and repopulated on every page change

See studio_menu_plan in PLAN.md.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QLabel, QMainWindow, QSizePolicy, QToolBar, QWidget


def _spacer() -> QWidget:
    """Pushes whatever follows to the right edge."""
    widget = QWidget()
    widget.setObjectName("Spacer")
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return widget


class NavBar(QToolBar):
    """BAR 1. One button per page, exclusive, always visible."""

    def __init__(self, window: QMainWindow, pages, on_select) -> None:
        super().__init__("Pages", window)
        self.setObjectName("NavBar")
        self.setMovable(False)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)

        self._group = QActionGroup(window)
        self._group.setExclusive(True)
        self.actions_by_index: list[QAction] = []

        for index, page in enumerate(pages):
            action = QAction(page.label, window)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked, i=index: on_select(i))
            self._group.addAction(action)
            self.addAction(action)
            self.actions_by_index.append(action)

        self.addWidget(_spacer())
        self.connection_label = QLabel("○ no robot   ")
        self.addWidget(self.connection_label)

    def check(self, index: int) -> None:
        self.actions_by_index[index].setChecked(True)


class ContextBar(QToolBar):
    """BAR 2. Rebuilt from scratch whenever BAR 1 changes."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__("Actions", window)
        self.setObjectName("ContextBar")
        self.setMovable(False)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._window = window

    def show_page(self, page) -> None:
        self.clear()
        for action in page.build_actions(self._window):
            if action is None:
                self.addSeparator()
            else:
                self.addAction(action)

        self.addWidget(_spacer())
        self.addWidget(QLabel(f"{page.status}   "))
