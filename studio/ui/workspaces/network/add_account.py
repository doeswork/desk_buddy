"""The Add Account page: name a new account and say what it may reach.

Reached from the Accounts page's Add Account button, not from the side panel.
It is a step in a task rather than a place in the app, so it is not somewhere
the user should be able to wander into with nothing in mind — but it is still
a page, with the room to explain what the choice on it means.

View only. What makes a valid name, and what each access level grants, are
decided in `studio.services.network.broker.accounts`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ....models.config.mqtt_users import (
    NAME_RULE,
    generate_password,
    validate_name,
)
from ....services.network.broker import system
from ...components import Card, Column
from ...pages.base import Page
from ...theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING


class AddAccountPage(Page):
    key = "add_account"
    label = "Add User"

    title = "Add User"
    subtitle = "A name, and how much of the broker this user may reach."

    def __init__(self, workspace=None) -> None:
        super().__init__(workspace)
        self._name = ""
        self._full_access = False
        # Set after a failed attempt, so the reason sits next to the field
        # rather than in a dialog the user has to dismiss to fix it.
        self._problem = ""
        self._form: AccountForm | None = None

    # ---- body ------------------------------------------------------------
    def build_page(self) -> QWidget:
        self._form = AccountForm(
            self._name,
            self._full_access,
            self._problem,
            on_name_changed=self._name_changed,
            on_access_changed=self._access_changed,
            on_submit=self.create,
            on_cancel=self.cancel,
        )
        return Column(self._form)

    def _name_changed(self, text: str) -> None:
        # Held on the page, not read out of the widget at submit time: the
        # form is rebuilt whenever the page is, and a value that lives only in
        # a widget would not survive that.
        self._name = text

    def _access_changed(self, full_access: bool) -> None:
        self._full_access = full_access

    # ---- actions ---------------------------------------------------------
    def enter(self) -> None:
        """Start from empty. Called by the workspace on the way in."""
        self._name = ""
        self._full_access = False
        self._problem = ""
        self.rebuild()
        if self._form is not None:
            self._form.focus_name()

    def create(self) -> None:
        name = self._name.strip()

        # Asked before doing: being told the rule while still on the form
        # beats being told after the attempt.
        taken = {account.name for account in system.accounts()}
        problem = validate_name(name, taken)
        if problem:
            self._fail(problem)
            return

        # Generated here rather than typed: the password is written straight
        # into the broker and shown once, so there is nothing for the user to
        # invent and nothing weak to pick.
        password = generate_password()
        topics = "#" if self._full_access else f"{name}/#"
        problem = self.workspace.create_account(name, password, topics)
        if problem:
            self._fail(problem)
            return

        # The password exists only in the variable above — the broker keeps a
        # hash — so handing it straight to the page that shows it is the only
        # way it survives. Set before navigating: Accounts is already built by
        # this point, but state-then-navigate is the clearer order.
        accounts = self.workspace.find("accounts")
        accounts.created(name, password)
        self.workspace.go_to("accounts")

    def cancel(self) -> None:
        self.workspace.go_to("accounts")

    def _fail(self, problem: str) -> None:
        self._problem = problem
        self.rebuild()
        if self._form is not None:
            self._form.focus_name()


class AccountForm(QWidget):
    """The name field and the access choice."""

    def __init__(self, name: str, full_access: bool, problem: str,
                 *, on_name_changed, on_access_changed,
                 on_submit, on_cancel) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, CARD_MARGIN_V
        )
        layout.setSpacing(CARD_SPACING * 2)

        heading = QLabel("Name")
        heading.setObjectName("CardTitle")
        layout.addWidget(heading)

        self._name = QLineEdit(name)
        self._name.setPlaceholderText("robot-1")
        self._name.textChanged.connect(on_name_changed)
        layout.addWidget(self._name)

        rule = QLabel(problem or NAME_RULE)
        rule.setObjectName("FieldError" if problem else "CardBody")
        rule.setWordWrap(True)
        layout.addWidget(rule)

        layout.addSpacing(CARD_SPACING * 3)

        access = QLabel("Access")
        access.setObjectName("CardTitle")
        layout.addWidget(access)

        # Radio buttons rather than a checkbox: full access is not "the option
        # with a bit more", it is the other of two answers, and both deserve
        # to say what they mean.
        self._group = QButtonGroup(self)
        own = QRadioButton("Own topics only")
        whole = QRadioButton("Full access to every topic")
        for index, button in enumerate((own, whole)):
            button.setCursor(Qt.PointingHandCursor)
            self._group.addButton(button, index)
            layout.addWidget(button)
            layout.addWidget(self._caption(index))
        (whole if full_access else own).setChecked(True)
        self._group.idToggled.connect(
            lambda index, checked: on_access_changed(index == 1) if checked else None
        )

        # The form's own buttons, on the form. BAR 2 carries what the whole
        # workspace can do; finishing or abandoning this one form is not that,
        # and putting it up there would make the bar change shape per page.
        layout.addSpacing(CARD_SPACING * 3)
        buttons = QHBoxLayout()
        buttons.setSpacing(CARD_SPACING * 2)

        create = QPushButton("Create User")
        create.setObjectName("ContextPrimary")
        create.setCursor(Qt.PointingHandCursor)
        create.clicked.connect(on_submit)

        cancel = QPushButton("Cancel")
        cancel.setObjectName("ContextAction")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(on_cancel)

        buttons.addWidget(create)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        # Enter submits: the name field is the only thing to type in, so
        # reaching for the mouse to finish is a step that need not exist.
        self._name.returnPressed.connect(on_submit)

    @staticmethod
    def _caption(index: int) -> QLabel:
        text = (
            "Reaches only its own tree, so one robot's traffic can never "
            "appear under another's. This is what a desk buddy wants."
            if index == 0 else
            "Sees the whole broker. What a vision server or a web app needs, "
            "and more than a single robot should ever have."
        )
        caption = QLabel(text)
        caption.setObjectName("CardBody")
        caption.setWordWrap(True)
        caption.setContentsMargins(CARD_MARGIN_H, 0, 0, CARD_SPACING)
        return caption

    def focus_name(self) -> None:
        self._name.setFocus()
        self._name.selectAll()
