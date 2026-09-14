"""the workspace bar: which top-level area of the app you are in.

The first of the app's three bars, under the menu bar and above the toolbar.
One button per workspace, exclusive, always visible. Never changes. Switching
between the pages *inside* a workspace is the side panel's job.

Its right-hand end carries the two things that are true no matter where you
are: whether the broker is up, and which robot you are driving. They live here
rather than in the Network workspace because they stay relevant while you are
somewhere else — the robot a workflow is about to run on matters most on the
Workflows page, which has no picker of its own.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QMainWindow,
    QStyle,
    QToolBar,
)

from ...models.config.current_robot import current_robot
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
        self._buttons_by_action: dict[QAction, object] = {}
        self._workspaces = list(workspaces)

        for index, workspace in enumerate(self._workspaces):
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
                self._buttons_by_action[action] = button

        self.addWidget(spacer())

        self.broker_label = QLabel()
        self.broker_label.setObjectName("StatusChip")
        self.addWidget(self.broker_label)
        self.set_broker("○ no broker")

        # Which robot every workspace is talking to, chosen here.
        #
        # It was a status chip that said "no robot" and never said anything
        # else — nothing ever called its setter. Worse, the one real control
        # was a picker buried on the Calibration steps, so a workflow could
        # be run with no way to see which machine it was addressed to.
        #
        # The selection is app-wide, so the place to make it is the bar that
        # is on screen in every workspace.
        self.robot_picker = QComboBox()
        self.robot_picker.setObjectName("RobotPicker")
        self.robot_picker.setCursor(Qt.PointingHandCursor)
        self.robot_picker.setToolTip(
            "The robot every workspace sends commands to."
        )
        self.robot_picker.currentIndexChanged.connect(self._robot_chosen)
        self.addWidget(self.robot_picker)
        self.refresh_robots()
        # Kept current when something else changes the selection — the
        # Calibration picker, or a robot marked on Network → Robots.
        self._unwatch = current_robot().watch(lambda _name: self.refresh_robots())

    # ---- the robot picker -------------------------------------------------
    def refresh_robots(self) -> None:
        """Rebuild the list, keeping it on whatever is selected now."""
        selection = current_robot()
        available = selection.available()
        current = selection.name

        # Signals off: repopulating moves the index, and reacting to that
        # would write the list's own first entry back as the user's choice.
        self.robot_picker.blockSignals(True)
        self.robot_picker.clear()
        if not available:
            self.robot_picker.addItem("no robot", "")
            self.robot_picker.setEnabled(False)
        else:
            for robot in available:
                self.robot_picker.addItem(robot.display_name, robot.name)
            self.robot_picker.setEnabled(True)
            index = self.robot_picker.findData(current)
            if index >= 0:
                self.robot_picker.setCurrentIndex(index)
        self.robot_picker.blockSignals(False)

    def _robot_chosen(self, index: int) -> None:
        if index < 0:
            return
        name = str(self.robot_picker.itemData(index) or "")
        if name:
            current_robot().select(name)

    def check(self, index: int) -> None:
        self.actions_by_index[index].setChecked(True)

    # ---- The two always-true facts --------------------------------------
    # Both take finished text. Deciding what "online" means, or how to word it,
    # belongs to the service that knows — not to the bar that shows it.
    def set_broker(self, text: str) -> None:
        self.broker_label.setText(text)

    def set_issue(self, workspace_key: str, issue: bool, detail: str = "") -> None:
        """Put a theme-aware warning icon on one workspace tab."""
        for action, workspace in zip(self.actions_by_index, self._workspaces):
            if workspace.key == workspace_key:
                self._set_action_issue(
                    action, issue, detail, self._buttons_by_action.get(action)
                )
                return

    @staticmethod
    def _set_action_issue(action: QAction, issue: bool, detail: str, button=None) -> None:
        if issue:
            action.setIcon(QApplication.style().standardIcon(QStyle.SP_MessageBoxWarning))
            action.setToolTip(detail)
            if button is not None:
                button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        else:
            action.setIcon(QIcon())
            action.setToolTip("")
            if button is not None:
                button.setToolButtonStyle(Qt.ToolButtonTextOnly)
