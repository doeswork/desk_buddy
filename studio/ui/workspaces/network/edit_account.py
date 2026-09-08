"""The Edit Account page: what topics one account may reach.

Reached from the Edit action on an Accounts row, not from the side panel —
same reasoning as Add Account: a step in a task, not a place to browse into.

Topics take effect as they are added or removed rather than behind a Save —
each one is already a live edit to the ACL file (see
`studio.models.config.mqtt_users.Users.add_topic` / `remove_topic`), so a separate
save step would be a promise the page cannot actually keep: the account is
already different the moment a topic is added, whether or not the user then
leaves by Cancel. Done is offered because leaving needs a way to leave, not
because anything is pending.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....models.config.mqtt_topics import TOPIC_RULE, validate_topic
from ....services.network.broker import system
from ...components import Card, Column
from ...pages.base import Page
from ...theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING


class EditAccountPage(Page):
    key = "edit_account"
    label = "Edit User"

    def __init__(self, workspace=None) -> None:
        super().__init__(workspace)
        self._name = ""
        self._new_topic = ""
        # Set after a failed attempt, so the reason sits next to the field
        # rather than in a dialog the user has to dismiss to fix it.
        self._problem = ""
        self._form: TopicForm | None = None

    @property
    def title(self) -> str:
        return f"Edit {self._name}" if self._name else "Edit User"

    @property
    def subtitle(self) -> str:
        return "Add or remove the topics this user may publish and subscribe to."

    # ---- body ------------------------------------------------------------
    def build_page(self) -> QWidget:
        account = self._account()
        if account is None:
            # The account was removed from elsewhere while this page was open
            # — another tab of the same Studio, say. Nothing to edit; back out
            # rather than show a form for something that no longer exists.
            return Column(Card(
                "This user no longer exists",
                "It may have been removed. Go back to see who is left.",
            ))

        self._form = TopicForm(
            account.topics,
            self._new_topic,
            self._problem,
            on_topic_changed=self._topic_changed,
            on_add=self.add_topic,
            on_remove=self.remove_topic,
            on_done=self.done,
        )
        return Column(self._form)

    def _account(self):
        return next(
            (a for a in self.workspace.accounts() if a.name == self._name), None
        )

    def _topic_changed(self, text: str) -> None:
        self._new_topic = text

    # ---- actions ---------------------------------------------------------
    def open_for(self, name: str) -> None:
        """Start editing one account. Called by the row that opens this page."""
        self._name = name
        self._new_topic = ""
        self._problem = ""

    def enter(self) -> None:
        """Re-read the account fresh each time the page is opened."""
        self._problem = ""
        self.rebuild()
        if self._form is not None:
            self._form.focus_topic()

    def add_topic(self) -> None:
        topic = self._new_topic.strip()
        account = self._account()
        problem = validate_topic(topic, account.topics if account else ())
        if problem:
            self._fail(problem)
            return

        problem = self._set_topics((*account.topics, topic) if account else (topic,))
        if problem:
            self._fail(problem)
            return

        self._new_topic = ""
        self._problem = ""
        self.rebuild()
        if self._form is not None:
            self._form.focus_topic()

    def remove_topic(self, topic: str) -> None:
        account = self._account()
        if account is None:
            return
        remaining = tuple(t for t in account.topics if t != topic)
        problem = self._set_topics(remaining)
        if problem:
            QMessageBox.warning(self.widget(), "Could not remove the topic", problem)
            return
        self.rebuild()

    def _set_topics(self, topics) -> str:
        """Write the ACL and refresh the workspace's cached account list."""
        problem = system.set_topics(self._name, topics)
        self.workspace.refresh()
        return problem

    def done(self) -> None:
        self.workspace.go_to("accounts")

    def _fail(self, problem: str) -> None:
        self._problem = problem
        self.rebuild()
        if self._form is not None:
            self._form.focus_topic()


class TopicForm(QWidget):
    """The current topic list, and the field to add another."""

    def __init__(self, topics: tuple[str, ...], new_topic: str, problem: str,
                 *, on_topic_changed, on_add, on_remove, on_done) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, CARD_MARGIN_V
        )
        layout.setSpacing(CARD_SPACING * 2)

        heading = QLabel("Topics")
        heading.setObjectName("CardTitle")
        layout.addWidget(heading)

        if topics:
            layout.addWidget(TopicList(topics, on_remove))
        else:
            empty = QLabel(
                "No topics yet. This user can connect but cannot publish "
                "or subscribe anywhere until one is added below."
            )
            empty.setObjectName("CardBody")
            empty.setWordWrap(True)
            layout.addWidget(empty)

        layout.addSpacing(CARD_SPACING * 3)

        add_heading = QLabel("Add a topic")
        add_heading.setObjectName("CardTitle")
        layout.addWidget(add_heading)

        row = QHBoxLayout()
        row.setSpacing(CARD_SPACING * 2)
        self._topic = QLineEdit(new_topic)
        self._topic.setPlaceholderText("robot-1/status")
        self._topic.textChanged.connect(on_topic_changed)
        self._topic.returnPressed.connect(on_add)
        row.addWidget(self._topic, 1)

        add = QPushButton("Add Topic")
        add.setObjectName("ToolbarAction")
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(on_add)
        row.addWidget(add)
        layout.addLayout(row)

        rule = QLabel(problem or TOPIC_RULE)
        rule.setObjectName("FieldError" if problem else "CardBody")
        rule.setWordWrap(True)
        layout.addWidget(rule)

        layout.addSpacing(CARD_SPACING * 3)
        done_row = QHBoxLayout()
        done = QPushButton("Done")
        done.setObjectName("ToolbarPrimary")
        done.setCursor(Qt.PointingHandCursor)
        done.clicked.connect(on_done)
        done_row.addWidget(done)
        done_row.addStretch(1)
        layout.addLayout(done_row)

    def focus_topic(self) -> None:
        self._topic.setFocus()
        self._topic.selectAll()


class TopicList(QListWidget):
    """Every topic this account has, each with its own Remove."""

    def __init__(self, topics: tuple[str, ...], on_remove) -> None:
        super().__init__()
        self.setObjectName("AccountList")
        self.setSelectionMode(QListWidget.NoSelection)

        for topic in topics:
            item = QListWidgetItem()
            self.addItem(item)
            row = self._row(topic, on_remove)
            item.setSizeHint(row.sizeHint())
            self.setItemWidget(item, row)

        self.setFixedHeight(
            sum(self.sizeHintForRow(r) for r in range(self.count()))
            + 2 * self.frameWidth()
        )

    @staticmethod
    def _row(topic: str, on_remove) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)

        label = QLabel(topic)
        label.setObjectName("CommandText")
        layout.addWidget(label, 1)

        remove = QLabel("Remove")
        remove.setObjectName("RowActionBad")
        remove.setCursor(Qt.PointingHandCursor)
        remove.setToolTip(f"Remove {topic}")
        remove.mousePressEvent = lambda event: on_remove(topic)
        layout.addWidget(remove)

        return holder
