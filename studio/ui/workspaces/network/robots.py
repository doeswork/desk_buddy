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

import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
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

from ....services.network import studio_credentials
from ....services.network import robot_endpoint
from ....services.network.broker import system
from ....models.config.mqtt_users import generate_password, users
from ....models.config.robot_profiles import RobotProfile, profiles
from ....models.config.robots import robots
from ...components import Card, Column
from ...components.robot_profile_dialog import RobotProfileDialog
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
            sections.append(
                RobotTable(entries, on_remove=self.unmark, on_edit=self.edit_profile)
            )

        return Column(*sections)

    def _candidates(self) -> list[str]:
        """Accounts that could still be marked as a robot."""
        marked = {robot.name for robot in robots().all()}
        studio_account, _ = studio_credentials()
        return [
            account.name for account in system.accounts()
            if account.name != studio_account and account.name not in marked
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

    def edit_profile(self, robot) -> None:
        existing = profiles().by_user(robot.name)
        host, port = robot_endpoint()
        if not host:
            host = self.workspace.broker_host() if self.workspace is not None else ""
        if not port:
            port = 1883
        if existing is None:
            account = next((item for item in system.accounts() if item.name == robot.name), None)
            existing = RobotProfile(
                profile_id=uuid.uuid4().hex,
                name=robot.display_name,
                broker_server=host,
                broker_port=port,
                mqtt_user=robot.name,
                mqtt_password=getattr(account, "password", "") or generate_password(),
                client_id=robot.name,
                broker_kind="local",
            )
        dialog = RobotProfileDialog(
            self.widget(),
            profile=existing,
            profiles=profiles().all(),
            accounts=system.accounts(),
            default_server=host,
            default_port=port,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        profile = dialog.profile()
        profiles().save(profile)
        if profile.broker_kind == "local":
            change = self.workspace.create_account(
                profile.mqtt_user,
                profile.mqtt_password,
                f"{profile.mqtt_user}/#",
            )
            if not change.changed:
                QMessageBox.warning(
                    self.widget(), "Profile saved", change.problem or "The broker account could not be updated."
                )
            else:
                users().record(profile.mqtt_user, profile.mqtt_password)
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
        mark.setObjectName("ToolbarPrimary")
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

    COLUMNS = ("Robot", "User", "Connection profile", "")
    LABEL = 0
    ACCOUNT = 1
    PROFILE = 2
    ACTIONS = 3

    def __init__(self, entries: list, *, on_remove, on_edit=None) -> None:
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
            profile = profiles().by_user(robot.name)
            summary = (
                f"{profile.broker_server}:{profile.broker_port} · "
                f"{profile.mqtt_user} · {'TLS' if profile.tls else 'plain'}"
                if profile is not None else "Not configured"
            )
            profile_item = QTableWidgetItem(summary)
            profile_item.setToolTip(
                "Passwords are stored locally and remain masked."
                if profile is not None else "Configure this robot before flashing."
            )
            self.setItem(row, self.PROFILE, profile_item)
            actions = (
                self._actions(robot, on_remove, on_edit)
                if on_edit is not None
                else self._unmark_button(robot.name, on_remove)
            )
            self.setCellWidget(row, self.ACTIONS, actions)

        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Fixed)
        header.setSectionResizeMode(self.ACCOUNT, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.PROFILE, QHeaderView.Stretch)
        self.setColumnWidth(self.LABEL, self._header_width("Robot") * 2)
        self.setColumnWidth(self.ACTIONS, self._header_width("Edit") + self._header_width("Unmark"))
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

    @staticmethod
    def _actions(robot, on_remove, on_edit) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(ROW_PADDING // 2, 0, ROW_PADDING, 0)
        edit = QLabel("Edit")
        edit.setObjectName("RowAction")
        edit.setCursor(Qt.PointingHandCursor)
        edit.setToolTip(f"Edit {robot.display_name}'s saved connection profile")
        edit.mousePressEvent = lambda _event: on_edit(robot)
        layout.addWidget(edit)
        unmark = QLabel("Unmark")
        unmark.setObjectName("RowActionBad")
        unmark.setCursor(Qt.PointingHandCursor)
        unmark.setToolTip(f"Stop treating {robot.name} as a robot")
        unmark.mousePressEvent = lambda _event: on_remove(robot.name)
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
