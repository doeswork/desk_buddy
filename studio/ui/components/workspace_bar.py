"""the workspace bar: which top-level area of the app you are in.

The first of the app's three bars, under the menu bar and above the toolbar.
One button per workspace, exclusive, always visible. Never changes. Switching
between the pages *inside* a workspace is the side panel's job.

Its right-hand end carries the two facts that are true no matter where you
are: is the broker up, and is the robot there. They live here rather than in
the Network workspace because they stay relevant while you are somewhere else
— a robot that drops offline matters most when you are driving it by hand
from the tray's Manual Control tab.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QLabel, QMainWindow, QToolBar

from .spacer import spacer


class WorkspaceBar(QToolBar):
    def __init__(self, window: QMainWindow, workspaces, on_select) -> None:
        super().__init__("Workspaces", window)
        self.setObjectName("WorkspaceBar")
        self.setMovable(False)
        self.layout().setSpacing(0)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)

        self._group = QActionGroup(window)
        self._group.setExclusive(True)
        self.actions_by_index: list[QAction] = []

        for index, workspace in enumerate(workspaces):
            action = QAction(workspace.label, window)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked, i=index: on_select(i))
            self._group.addAction(action)
            self.addAction(action)
            self.actions_by_index.append(action)
            # QSS has no `cursor` property — Qt cursors are only set through
            # QWidget.setCursor(), so the actual QToolButton addAction()
            # creates has to be fetched and given one directly.
            button = self.widgetForAction(action)
            if button is not None:
                button.setCursor(Qt.PointingHandCursor)

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
