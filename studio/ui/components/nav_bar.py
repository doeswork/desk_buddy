"""BAR 1 — the page switcher.

One button per page, exclusive, always visible. Never changes.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QLabel, QMainWindow, QToolBar

try:
    from .spacer import spacer
except ImportError:
    from spacer import spacer


class NavBar(QToolBar):
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

        self.addWidget(spacer())
        self.connection_label = QLabel("○ no robot   ")
        self.addWidget(self.connection_label)

    def check(self, index: int) -> None:
        self.actions_by_index[index].setChecked(True)
