"""The Accounts page: who may connect, and which topics they may use.

View only. Which topics an account reaches, what a valid name is, and how a
password is made are all decided in `studio.services.network.accounts`.

Add Account, Reset Password and Remove Account live here rather than on BAR
2: BAR 2 is the workspace's own controls, the same on every Network page, and
what an account may do is a property of one row in this page's table, not of
the workspace. Add sits at the top right of the page, and Reset / Remove sit
on the row they act on.

The page has two states. Normally it is the table of accounts; after one is
created or reset it shows that account's credentials instead, because a
password is shown exactly once and the user has to be able to copy it before
moving on.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ....services.network import accounts as service
from ....services.network import broker_commands as commands
from ...components import Card, Column
from ...pages.base import Page
from ...theme.metrics import (
    CARD_MARGIN_H,
    CARD_MARGIN_V,
    CARD_SPACING,
    ROW_PADDING,
)


class AccountsPage(Page):
    key = "accounts"
    label = "Accounts"

    title = "Accounts"
    subtitle = "Each one gets its own topics, so two robots never hear each other."

    def __init__(self, workspace=None) -> None:
        super().__init__(workspace)
        # Set while credentials are on screen; cleared when the user is done.
        self._credentials: tuple[str, str] | None = None

    # ---- body ------------------------------------------------------------
    def build_page(self) -> QWidget:
        if not self.workspace.running():
            return Column(Card(
                "The broker is not running",
                "Accounts live in the broker's password file. Start the broker "
                "on the Broker page to add one.",
            ))

        if self._credentials is not None:
            name, password = self._credentials
            return Column(CredentialsCard(
                name,
                password,
                commands.DEFAULT_HOST,
                self.workspace.broker().port or commands.DEFAULT_PORT,
                on_done=self._dismiss,
            ))

        # Reading the list is what creates Studio's account on a fresh broker,
        # so it happens before anything below asks whether there is a password
        # to show.
        entries = self.workspace.accounts()
        sections = []

        if self.workspace.studio_problem:
            sections.append(Card(
                "Studio has no account of its own",
                self.workspace.studio_problem,
            ))
        elif self.workspace.studio_password:
            password = self.workspace.studio_password
            self.workspace.studio_password = ""     # shown once, like any other
            sections.append(CredentialsCard(
                service.STUDIO_NAME,
                password,
                commands.DEFAULT_HOST,
                self.workspace.broker().port or commands.DEFAULT_PORT,
                intro="Studio created its own account so it can reach the "
                      "broker. Copy the password if you want it — like every "
                      "account, it cannot be shown again.",
            ))

        port = self.workspace.broker().port or commands.DEFAULT_PORT
        sections.append(self._toolbar())
        sections.append(Card(
            "Connect to this broker",
            f"{commands.DEFAULT_HOST}:{port}, with the username and "
            "password below.",
        ))
        sections.append(AccountTable(
            entries,
            on_reset=self.reset_password,
            on_remove=self.remove_account,
        ))
        return Column(*sections)

    def _toolbar(self) -> QWidget:
        """Add Account, at the top right of the page's own content.

        Not BAR 2: this button belongs to the accounts list, and putting it
        up in the workspace's bar would make Add Account exist even on pages
        that have no account list to add to.
        """
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)

        add = QPushButton("Add Account")
        add.setObjectName("ContextPrimary")
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(self.workspace.add_account)
        layout.addWidget(add)
        return holder

    # ---- actions ---------------------------------------------------------
    def created(self, name: str, password: str) -> None:
        """Called by the Add Account page once the account exists."""
        self._show(name, password)

    def reset_password(self, name: str) -> None:
        password, problem = service.reset_password(name)
        if problem:
            QMessageBox.warning(
                self.widget(), "Could not reset the password", problem
            )
            return

        self._show(name, password)

    def remove_account(self, name: str) -> None:
        parent = self.widget()

        # Deleting a credential cannot be undone — the hash is gone and every
        # device using it stops connecting — so it is worth one question.
        confirm = QMessageBox.question(
            parent,
            f"Remove {name}?",
            f"{name} will no longer be able to connect, and anything already "
            "using its password will stop working. This cannot be undone.",
        )
        if confirm != QMessageBox.Yes:
            return

        problem = service.remove(name)
        if problem:
            QMessageBox.warning(parent, "Could not remove the account", problem)
            return

        self.rebuild()

    def _show(self, name: str, password: str) -> None:
        self._credentials = (name, password)
        self.rebuild()

    def _dismiss(self) -> None:
        self._credentials = None
        self.rebuild()


class AccountTable(QTableWidget):
    """Every account, what a client needs to connect as one, and its actions.

    Reset and Remove sit on the row they act on rather than needing a
    selection first — a click does the whole thing, there is no state in
    between where a button up in a toolbar would still need to catch up.
    """

    # Host and port are the same for every row — they belong above the table
    # once, not repeated on every line — so a row is just the account.
    COLUMNS = ("Username", "Access", "Topics", "")
    # Index of the actions column, named: clearer at the call sites below than
    # a bare integer, and the one place that has to change if a column is added.
    ACTIONS = 3

    def __init__(self, entries: list, *, on_reset, on_remove) -> None:
        super().__init__(len(entries), len(self.COLUMNS))
        self.setObjectName("AccountTable")
        self.setHorizontalHeaderLabels(self.COLUMNS)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)

        # No row selection: each row's actions are its own buttons, so there
        # is nothing left for a click on the row itself to mean.
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)

        for row, account in enumerate(entries):
            values = (account.name, account.access, account.topics)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if account.is_studio:
                    # Studio's own row reads as chrome rather than as one of
                    # the user's: it is there, but it is not theirs to manage.
                    item.setToolTip(service.STUDIO_DESCRIPTION)
                self.setItem(row, column, item)

            if account.is_studio:
                note = QLabel("Managed")
                note.setObjectName("CardBody")
                self.setCellWidget(row, self.ACTIONS, self._pad(note))
            else:
                self.setCellWidget(
                    row, self.ACTIONS,
                    self._row_actions(account.name, on_reset, on_remove),
                )

        # Topics is the one genuinely variable-length value — full_access
        # accounts show "#", others "name/#" — so it alone takes the slack.
        # Username and Access are fixed to their header's width; the actions
        # column is set from the widest cell widget it actually holds.
        #
        # ResizeToContents is deliberately not used for the actions column:
        # it sizes from QHeaderView's own content-size query, which — for a
        # cell widget — comes back narrower than the widget's own layout
        # reports, and Reset/Remove would render clipped inside a column that
        # measured itself too small for them.
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        for column in range(len(self.COLUMNS)):
            if column == 2:
                header.setSectionResizeMode(column, QHeaderView.Stretch)
            elif column == self.ACTIONS:
                header.setSectionResizeMode(column, QHeaderView.Fixed)
            else:
                header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.setColumnWidth(self.ACTIONS, self._actions_width())
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def _actions_width(self) -> int:
        widest = 0
        for row in range(self.rowCount()):
            cell = self.cellWidget(row, self.ACTIONS)
            if cell is not None:
                widest = max(widest, cell.sizeHint().width())
        return widest

    @staticmethod
    def _pad(widget: QWidget) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, ROW_PADDING, 0)
        layout.addWidget(widget)
        return holder

    def _row_actions(self, name: str, on_reset, on_remove) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, ROW_PADDING, 0)
        layout.setSpacing(0)

        # Compact row actions, not BAR-2-style buttons: the row already says
        # whose account this is, so the control only needs to name the verb.
        reset = self._button("Reset", "RowAction", f"Reset {name}'s password")
        reset.mousePressEvent = lambda event: on_reset(name)
        layout.addWidget(reset)

        remove = self._button("Remove", "RowActionBad", f"Remove {name}")
        remove.mousePressEvent = lambda event: on_remove(name)
        layout.addWidget(remove)

        return holder

    @staticmethod
    def _button(text: str, object_name: str, tooltip: str) -> QLabel:
        # A clickable label, not a QPushButton or QToolButton: both bake a
        # platform-default minimum-width margin into their sizeHint that no
        # amount of QSS padding overrides, which was most of why two
        # five-letter verbs were pushing the table past the page.
        #
        # A bare QLabel avoids the button floor, but giving it QSS padding
        # switches it onto Qt's styled-frame path, which adds its own
        # unpredictable margin on top — worse than what it replaced. So the
        # padding is real spacing in the layout instead, and the label sizes
        # to exactly its text.
        label = QLabel(text)
        label.setObjectName(object_name)
        label.setCursor(Qt.PointingHandCursor)
        label.setToolTip(tooltip)
        label.setContentsMargins(ROW_PADDING, 3, ROW_PADDING, 3)
        return label

    def _fit(self) -> None:
        """Match the widget's height to its rows.

        The table is page content, not a pane: it should show every account
        and let the page do the scrolling, rather than being a small box with
        a scrollbar inside a page that already has one.
        """
        # Row height set here rather than in the QSS: vertical cell padding
        # moves the text, not the row.
        height = self.fontMetrics().height() + 2 * ROW_PADDING
        for row in range(self.rowCount()):
            self.setRowHeight(row, height)
        rows = height * self.rowCount()
        self.setFixedHeight(
            rows + self.horizontalHeader().sizeHint().height() + 2 * self.frameWidth()
        )

    def showEvent(self, event) -> None:
        # Re-measured on show: row heights and the header's own height depend
        # on the stylesheet, which Qt applies when the widget is polished —
        # after __init__ has run, so a height taken there is computed against
        # the wrong font and padding.
        super().showEvent(event)
        self._fit()


class CredentialsCard(QWidget):
    """Shows a password once, because it can never be shown again.

    Mosquitto stores a hash. Nothing — not Studio, not the broker — can read
    this back, so the card is deliberately blunt about that.
    """

    DEFAULT_INTRO = (
        "Copy the password now — it is stored as a hash and cannot be shown "
        "again. If it is lost, reset it to get a new one."
    )

    def __init__(self, name: str, password: str, host: str, port: int,
                 parent: QWidget | None = None, *, intro: str = "",
                 on_done=None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, CARD_MARGIN_V
        )
        layout.setSpacing(CARD_SPACING * 2)

        title = QLabel(f"Account {name} is ready")
        title.setObjectName("CardTitle")
        layout.addWidget(title)

        warning = QLabel(intro or self.DEFAULT_INTRO)
        warning.setObjectName("CardBody")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        for label, value in (
            ("Host", host),
            ("Port", str(port)),
            ("Username", name),
            ("Password", password),
        ):
            layout.addLayout(self._field(label, value))

        # Dismissing the card is the card's own business.
        if on_done is not None:
            layout.addSpacing(CARD_SPACING * 2)
            row = QHBoxLayout()
            done = QPushButton("Done")
            done.setObjectName("ContextPrimary")
            done.setCursor(Qt.PointingHandCursor)
            done.clicked.connect(on_done)
            row.addWidget(done)
            row.addStretch(1)
            layout.addLayout(row)

    def _field(self, label: str, value: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(0)

        caption = QLabel(label)
        caption.setObjectName("CardBody")
        caption.setFixedWidth(84)
        row.addWidget(caption)

        field = QLineEdit(value)
        field.setObjectName("CommandText")
        field.setReadOnly(True)
        field.setCursorPosition(0)
        row.addWidget(field)

        copy = QPushButton("Copy")
        copy.setObjectName("ContextAction")
        copy.clicked.connect(lambda: self._copy(field, copy))
        row.addWidget(copy)
        return row

    @staticmethod
    def _copy(field: QLineEdit, button: QPushButton) -> None:
        field.selectAll()
        field.copy()
        field.setCursorPosition(0)
        button.setText("Copied")
