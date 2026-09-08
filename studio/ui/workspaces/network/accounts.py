"""The Accounts page: who may connect, and which topics they may use.

View only. Which topics an account reaches, what a valid name is, and how a
password is made are all decided in `studio.services.network.broker.accounts`.

Add Account, Reset Password and Remove Account live here rather than on BAR
2: BAR 2 is the workspace's own controls, the same on every Network page, and
what an account may do is a property of one row in this page's table, not of
the workspace. Add sits at the top right of the page, and Reset / Remove sit
on the row they act on.

Normally this page is the table of accounts. After one is created, or when the
user chooses Show, it presents that account's recorded credentials instead.
Mosquitto owns the account and stores its hash; Studio's private record keeps
only passwords Studio itself generated.
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

from ....services.network import SYSTEM_PORT, lan_address
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
    label = "Users"

    title = "Users"
    subtitle = "Each user gets its own topics, so two robots never hear each other."

    def __init__(self, workspace=None) -> None:
        super().__init__(workspace)
        # Set while credentials are on screen; cleared when the user is done.
        self._credentials: tuple[str, str] | None = None

    # ---- body ------------------------------------------------------------
    def build_page(self) -> QWidget:
        # Read from the broker's own files, so an account made by hand with
        # mosquitto_passwd is as visible as one Studio created.
        entries = self.workspace.accounts()
        port = self.workspace.system_broker().port or SYSTEM_PORT
        # The address a client actually connects on, which is the LAN one
        # the broker binds to — not loopback, which no robot can reach.
        host = self.workspace.broker_host()
        if not host and not self.workspace.wsl_robot_access_available():
            host = lan_address() or "127.0.0.1"
        sections = []

        if self._credentials is not None:
            name, password = self._credentials
            applying = self.workspace.account_reload.running
            waiting = self.workspace.account_reload_pending()
            if applying:
                title = f"User {name} is saved"
                intro = (
                    "Copy this password now. Studio is applying the broker "
                    "change in the background and keeps a private local copy."
                )
            elif waiting:
                title = f"User {name} is saved"
                intro = (
                    "Copy this password now. The broker reload still needs "
                    "attention, but this credential is preserved and will not "
                    "be regenerated."
                )
            else:
                title = f"User {name} is ready"
                intro = ""
            sections.append(CredentialsCard(
                name,
                password,
                host,
                port,
                title=title,
                intro=intro,
                on_done=self._dismiss,
            ))

        reload_card = self._reload_card()
        if reload_card is not None:
            sections.append(reload_card)

        if self.workspace.running() and host:
            sections.append(Card(
                "Connect to this broker",
                f"{host}:{port}, with a username and password below.",
            ))
        elif self.workspace.running():
            sections.append(Card(
                "Robot access is not ready",
                "Open Network → Broker and enable robot access before using "
                "these credentials on a device outside WSL.",
            ))
        else:
            sections.append(Card(
                "The broker is not running",
                "Nothing can connect until this machine's Mosquitto is "
                "started — see the Broker page.",
            ))

        access = self.workspace.write_access()
        if not access.allowed:
            sections.append(Card(
                "Studio cannot change these users",
                f"{access.reason} Add User and Remove are unavailable until "
                "Studio is given access — Network → Broker has the two "
                "commands that grant it. Accounts can still be created by "
                "hand with mosquitto_passwd.",
            ))

        busy = self.workspace.account_actions_busy()
        sections.append(AccountTable(
            entries,
            on_show=self.show_account,
            on_edit=self.edit_account if access.allowed and not busy else None,
            on_remove=self.remove_account if access.allowed and not busy else None,
            busy=busy,
        ))
        return Column(*sections)

    def build_header_actions(self) -> QWidget | None:
        """Add User, level with the subtitle at the header's right edge.

        Not BAR 2: this button belongs to the accounts list, and putting it
        up in the workspace's bar would make Add Account exist even on pages
        that have no account list to add to. Hidden while there is nothing
        to add to yet, or a password already claiming the page's attention.

        Disabled rather than hidden when Studio may not write the broker's
        files: the action still exists and is the right one to want, so the
        page says it is unavailable and why instead of quietly omitting it.
        The card in the body carries the two commands that enable it.
        """
        if not self.workspace.running() or self._credentials is not None:
            return None

        add = QPushButton("Add User")
        add.setObjectName("ContextPrimary")
        access = self.workspace.write_access()
        if access.allowed:
            add.setCursor(Qt.PointingHandCursor)
            add.clicked.connect(self.workspace.add_account)
        else:
            add.setEnabled(False)
            add.setToolTip(
                f"{access.reason} See Network → Broker to grant access."
            )
        if self.workspace.account_actions_busy():
            add.setEnabled(False)
            add.setToolTip("Wait for the current broker user change to finish.")
        return add

    # ---- actions ---------------------------------------------------------
    def enter(self) -> None:
        """Re-stat the broker's files on the way in.

        The user may have run the grant commands in a terminal since this
        workspace last built, and a chown is invisible to a running app.
        Without this the page keeps saying Studio cannot write files it can
        write, with Add User disabled beside the proof it should not be.
        """
        self.workspace.recheck_access()

    def created(self, name: str, password: str) -> None:
        """Called by the Add Account page once the account exists."""
        self._show(name, password)

    def show_account(self, name: str, password: str) -> None:
        """Show a credential Studio recorded for an existing broker user."""
        self._show(name, password)

    def edit_account(self, name: str) -> None:
        """Hand over to the Edit Account page, for one account's topics."""
        edit = self.workspace.find("edit_account")
        edit.open_for(name)
        self.workspace.go_to("edit_account")

    def remove_account(self, name: str) -> None:
        parent = self.widget()

        # Every device using this credential stops connecting, and its topic
        # list goes with it, so it is worth one question.
        confirm = QMessageBox.question(
            parent,
            f"Remove {name}?",
            f"{name} will no longer be able to connect, and anything already "
            "using its password will stop working. This cannot be undone.",
        )
        if confirm != QMessageBox.Yes:
            return

        change = self.workspace.remove_account(name)
        if not change.changed:
            QMessageBox.warning(parent, "Could not remove the user", change.problem)

    def _reload_card(self) -> QWidget | None:
        pending = self.workspace.account_reload_pending()
        state = self.workspace.account_reload.status
        if self.workspace.account_reload.running:
            return Card("Applying broker user changes", state.message)
        if not pending:
            return None

        card = Card(
            "Broker user changes need attention",
            "The account files are saved, but Mosquitto has not reloaded them yet.",
        )
        detail = state.detail or self.workspace.account_problem
        if detail:
            reason = QLabel(detail)
            reason.setObjectName("FieldError")
            reason.setWordWrap(True)
            card.layout().addWidget(reason)
        retry = QPushButton("Retry applying changes")
        retry.setObjectName("ContextPrimary")
        retry.setEnabled(not self.workspace.setup.running)
        retry.clicked.connect(
            lambda: self.workspace.start_account_reload(retry=True)
        )
        card.layout().addWidget(retry)
        return card

    def _show(self, name: str, password: str) -> None:
        self._credentials = (name, password)
        self.rebuild()

    def _dismiss(self) -> None:
        self._credentials = None
        self.rebuild()


class AccountTable(QTableWidget):
    """Every account, what a client needs to connect as one, and its actions.

    Show, Edit and Remove sit on the row they act on rather than needing a
    selection first — a click does the whole thing, there is no state in
    between where a button up in a toolbar would still need to catch up.
    """

    # Host and port are the same for every row — they belong above the table
    # once, not repeated on every line — so a row is just the account.
    #
    # Access is left out for a different reason: it said "Full access" or
    # "Own topics", which the topic list beside it already showed ("#" versus
    # anything else). Two columns saying one thing is not worth the width the
    # topics themselves need to stay readable.
    COLUMNS = ("Username", "Topics", "")
    # Named indexes: clearer at the call sites below than bare integers, and
    # the one place that has to change if a column is added.
    TOPICS = 1      # takes whatever width the fixed columns leave
    ACTIONS = 2



    def __init__(self, entries: list, *, on_show, on_edit, on_remove,
                 busy: bool = False) -> None:
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
            values = (account.name, account.topics_display)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                # Username and Topics can both be elided when long, so the
                # tooltip is where the rest of the value still is.
                item.setToolTip(value)
                self.setItem(row, column, item)

            if not account.password and on_edit is None and on_remove is None:
                # Studio has not been granted write access. The rows are
                # still worth showing — this is the broker's real account
                # list — but there is nothing here to click.
                note = QLabel("Applying…" if busy else "Read only")
                note.setObjectName("CardBody")
                self.setCellWidget(row, self.ACTIONS, self._pad(note))
            else:
                self.setCellWidget(
                    row, self.ACTIONS,
                    self._row_actions(account, on_show, on_edit, on_remove),
                )

        # Topics is the one genuinely open-ended value — an account can carry
        # any number of filters, joined with commas — so it alone takes the
        # slack. Username and Access get a fixed width sized for what they
        # actually hold (a name, "Own topics"/"Full access"), not for their
        # header text: ResizeToContents measures the header label too, which
        # left Username wider than any name in it needs and Topics squeezed
        # for room that was never really in use.
        #
        # ResizeToContents is not used for the actions column either: it
        # sizes from QHeaderView's own content-size query, which — for a cell
        # widget — comes back narrower than the widget's own layout reports,
        # and Reset/Remove would render clipped inside a column that measured
        # itself too small for them.
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Fixed)
        header.setSectionResizeMode(self.TOPICS, QHeaderView.Stretch)
        # Username is sized to its header label. The label is the floor
        # because it can never elide — Qt does not shrink section text — so
        # anything under it would clip the column caption itself, which is
        # worse than clipping one long name.
        self.setColumnWidth(0, self._header_width("Username"))
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

    def _header_width(self, header_text: str) -> int:
        """A column's width, from its own header label.

        `2 * ROW_PADDING` accounts for the QSS padding both QHeaderView and
        QTableWidget::item carry on each side. A value longer than its header
        elides, with the full text left in the item's tooltip — cheaper than
        reserving column width every row pays for on the rare long one.
        """
        text_width = self.horizontalHeader().fontMetrics().horizontalAdvance(header_text)
        return text_width + 2 * ROW_PADDING

    @staticmethod
    def _pad(widget: QWidget) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, ROW_PADDING, 0)
        layout.addWidget(widget)
        return holder

    def _row_actions(self, account, on_show, on_edit, on_remove) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        # Several actions share this row, so the padding between them is
        # tighter than a single button's — an inner gap only needs to separate
        # two click targets, not frame one on its own.
        layout.setContentsMargins(ROW_PADDING // 2, 0, ROW_PADDING, 0)
        layout.setSpacing(0)

        # Compact row actions, not BAR-2-style buttons: the row already says
        # whose account this is, so the control only needs to name the verb.
        #
        # Mosquitto is authoritative for whether the account exists. Studio's
        # private record supplies only the generated password it cannot read
        # back from Mosquitto's hash.
        if account.password:
            show = self._button(
                "Show", "RowAction", f"Show {account.name}'s credentials"
            )
            show.mousePressEvent = (
                lambda event: on_show(account.name, account.password)
            )
            layout.addWidget(show)

        if on_edit is not None:
            edit = self._button(
                "Edit", "RowAction", f"Edit {account.name}'s topics"
            )
            edit.mousePressEvent = lambda event: on_edit(account.name)
            layout.addWidget(edit)

        if on_remove is not None:
            remove = self._button(
                "Remove", "RowActionBad", f"Remove {account.name}", last=True
            )
            remove.mousePressEvent = lambda event: on_remove(account.name)
            layout.addWidget(remove)

        return holder

    @staticmethod
    def _button(text: str, object_name: str, tooltip: str, *, last: bool = False) -> QLabel:
        # A clickable label, not a QPushButton or QToolButton: both bake a
        # platform-default minimum-width margin into their sizeHint that no
        # amount of QSS padding overrides, which was most of why two
        # five-letter verbs were pushing the table past the page.
        #
        # A bare QLabel avoids the button floor, but giving it QSS padding
        # switches it onto Qt's styled-frame path, which adds its own
        # unpredictable margin on top — worse than what it replaced. So the
        # padding is real spacing in the layout instead, and the label sizes
        # to exactly its text. Only the last of a row of these needs the full
        # gap on its right; the others sit close enough to read as one group.
        right = ROW_PADDING if last else ROW_PADDING // 2
        label = QLabel(text)
        label.setObjectName(object_name)
        label.setCursor(Qt.PointingHandCursor)
        label.setToolTip(tooltip)
        label.setContentsMargins(ROW_PADDING // 2, 3, right, 3)
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
    """Show a generated password kept in Studio's private record store."""

    DEFAULT_INTRO = (
        "Copy this password for the device. Mosquitto stores only its hash; "
        "Studio keeps this generated credential in its private local data "
        "file so you can retrieve it again."
    )

    def __init__(self, name: str, password: str, host: str, port: int,
                 parent: QWidget | None = None, *, title: str = "", intro: str = "",
                 on_done=None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, CARD_MARGIN_V
        )
        layout.setSpacing(CARD_SPACING * 2)

        heading = QLabel(title or f"User {name} is ready")
        heading.setObjectName("CardTitle")
        layout.addWidget(heading)

        warning = QLabel(intro or self.DEFAULT_INTRO)
        warning.setObjectName("CardBody")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        fields = []
        if host:
            fields.append(("Host", host))
        fields.extend((
            ("Port", str(port)),
            ("Username", name),
            ("Password", password),
        ))
        for label, value in fields:
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
