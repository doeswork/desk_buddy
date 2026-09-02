"""BAR 1 — the page switcher.

One button per page, exclusive, always visible. Never changes.

Its right-hand end carries the two facts that are true no matter which page you
are on: is the broker up, and is the robot there. They live here rather than on
the Network page because they stay relevant while you are somewhere else — a
robot that drops offline matters most when you are driving it from Manual.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QLabel, QMainWindow, QToolBar

from .spacer import spacer


class NavBar(QToolBar):
    def __init__(self, window: QMainWindow, pages, on_select) -> None:
        super().__init__("Pages", window)
        self.setObjectName("NavBar")
        self.setMovable(False)
        self.layout().setSpacing(0)
        self.layout().setContentsMargins(0, 0, 0, 0)
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

        self.broker_label = QLabel()
        self.broker_label.setObjectName("StatusChip")
        self.addWidget(self.broker_label)
        self.set_broker("○ no broker")

        self.connection_label = QLabel()
        self.connection_label.setObjectName("StatusChip")
        self.addWidget(self.connection_label)
        self.set_robot("○ no robot")

    def check(self, index: int) -> None:
        self.actions_by_index[index].setChecked(True)

    # ---- The two always-true facts --------------------------------------
    # Both take finished text. Deciding what "online" means, or how to word it,
    # belongs to the service that knows — not to the bar that shows it.
    def set_broker(self, text: str) -> None:
        self.broker_label.setText(text)

    def set_robot(self, text: str) -> None:
        self.connection_label.setText(text)
