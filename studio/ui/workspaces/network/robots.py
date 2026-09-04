"""The Robots page: which accounts are physical desk buddies.

An account is a credential; a robot is a credential Studio has marked as one.
Split from Accounts because marking one is a different question than creating
one — a vision server account never becomes a robot, and an account created
for a robot exists before it is marked. This page only marks and names; the
password and topics it connects with stay on Accounts.

View only. What counts as a valid robot — must be an existing, non-studio
account, not already marked — is decided in `studio.models.config.robots`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from ....models.config.mqtt_users import STUDIO_NAME, users
from ....models.config.robots import robots
from ...components import Card, Column
from ...pages.base import Page
from ...theme.metrics import ROW_PADDING


class RobotsPage(Page):
    key = "robots"
    label = "Robots"

    title = "Robots"
    subtitle = "Which users are physical desk buddies, and what to call them."

    def __init__(self, workspace=None) -> None:
        super().__init__(workspace)
        self._new_name = ""
        self._new_label = ""
        self._problem = ""

    # ---- body ------------------------------------------------------------
    def build_page(self) -> QWidget:
        entries = robots().all()
        sections = []

        candidates = self._candidates()
        if candidates:
            sections.append(MarkForm(
                candidates,
                self._new_name,
                self._new_label,
                self._problem,
                on_name_changed=self._name_changed,
                on_label_changed=self._label_changed,
                on_submit=self.mark,
            ))
        elif not entries:
            sections.append(Card(
                "No users to mark yet",
                "Create a user on Users first, then come back here to "
                "mark it as a robot.",
            ))

        if entries:
            sections.append(RobotTable(entries, on_remove=self.unmark))

        return Column(*sections)

    def _candidates(self) -> list[str]:
        """Accounts that could still be marked as a robot."""
        marked = {robot.name for robot in robots().all()}
        return [
            user.name for user in users().all()
            if user.name != STUDIO_NAME and user.name not in marked
        ]

    def _name_changed(self, name: str) -> None:
        self._new_name = name

    def _label_changed(self, label: str) -> None:
        self._new_label = label

    # ---- actions ---------------------------------------------------------
    def mark(self) -> None:
        created, problem = robots().add(self._new_name, self._new_label)
        if problem:
            self._problem = problem
            self.rebuild()
            return

        self._new_name = ""
        self._new_label = ""
        self._problem = ""
        self.rebuild()

    def unmark(self, name: str) -> None:
        problem = robots().remove(name)
        if problem:
            QMessageBox.warning(self.widget(), "Could not unmark robot", problem)
            return
        self.rebuild()


class MarkForm(QWidget):
    """Pick an existing account and give it a display name."""

    def __init__(self, candidates: list[str], name: str, label: str, problem: str,
                 *, on_name_changed, on_label_changed, on_submit) -> None:
        super().__init__()

        layout = QHBoxLayout(self)
        layout.setSpacing(ROW_PADDING)

        self._combo = QComboBox()
        self._combo.addItems(candidates)
        if name in candidates:
            self._combo.setCurrentText(name)
        else:
            on_name_changed(candidates[0])
        self._combo.currentTextChanged.connect(on_name_changed)
        layout.addWidget(self._combo)

        self._label = QLineEdit(label)
        self._label.setPlaceholderText("Display name (optional)")
        self._label.textChanged.connect(on_label_changed)
        self._label.returnPressed.connect(on_submit)
        layout.addWidget(self._label, 1)

        mark = QPushButton("Mark as Robot")
        mark.setObjectName("ContextPrimary")
        mark.setCursor(Qt.PointingHandCursor)
        mark.clicked.connect(on_submit)
        layout.addWidget(mark)

        if problem:
            error = QLabel(problem)
            error.setObjectName("FieldError")
            error.setWordWrap(True)
            layout.addWidget(error)


class RobotTable(QTableWidget):
    """Every marked robot, and its account name."""

    COLUMNS = ("Robot", "User", "")
    LABEL = 0
    ACCOUNT = 1
    ACTIONS = 2

    def __init__(self, entries: list, *, on_remove) -> None:
        super().__init__(len(entries), len(self.COLUMNS))
        self.setObjectName("AccountTable")
        self.setHorizontalHeaderLabels(self.COLUMNS)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)

        for row, robot in enumerate(entries):
            self.setItem(row, self.LABEL, QTableWidgetItem(robot.display_name))
            self.setItem(row, self.ACCOUNT, QTableWidgetItem(robot.name))
            self.setCellWidget(row, self.ACTIONS, self._unmark_button(robot.name, on_remove))

        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Fixed)
        header.setSectionResizeMode(self.ACCOUNT, QHeaderView.Stretch)
        self.setColumnWidth(self.LABEL, self._header_width("Robot") * 2)
        self.setColumnWidth(self.ACTIONS, self._header_width("Unmark") + 2 * ROW_PADDING)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def _header_width(self, header_text: str) -> int:
        text_width = self.horizontalHeader().fontMetrics().horizontalAdvance(header_text)
        return text_width + 2 * ROW_PADDING

    @staticmethod
    def _unmark_button(name: str, on_remove) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(ROW_PADDING // 2, 0, ROW_PADDING, 0)

        unmark = QLabel("Unmark")
        unmark.setObjectName("RowActionBad")
        unmark.setCursor(Qt.PointingHandCursor)
        unmark.setToolTip(f"Stop treating {name} as a robot")
        unmark.mousePressEvent = lambda event: on_remove(name)
        layout.addWidget(unmark)
        return holder

    def _fit(self) -> None:
        height = self.fontMetrics().height() + 2 * ROW_PADDING
        for row in range(self.rowCount()):
            self.setRowHeight(row, height)
        rows = height * self.rowCount()
        self.setFixedHeight(
            rows + self.horizontalHeader().sizeHint().height() + 2 * self.frameWidth()
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit()
