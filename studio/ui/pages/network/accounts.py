"""The Accounts card, and the dialog that shows a password exactly once.

View only. Which topics an account reaches, what a valid name is, and how a
password is made are all decided in `studio.network.accounts`.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....network import accounts as service
from ...components import Card
from ...theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING


def accounts_card(entries: list[service.Account]) -> QWidget:
    """Who can currently connect to this broker."""
    if not entries:
        return Card(
            "No accounts yet",
            "Nothing can connect to the broker until it has an account. "
            "Use Add Account above — each one gets its own topics, so two "
            "robots can never hear each other's traffic.",
            muted=False,
        )

    lines = [f"{account.name} — {account.description}" for account in entries]
    return Card(
        f"{len(entries)} account{'s' if len(entries) != 1 else ''}",
        "\n".join(lines),
        muted=False,
    )


class CredentialsDialog(QDialog):
    """Shows a password once, because it can never be shown again.

    Mosquitto stores a hash. Nothing — not Studio, not the broker — can read
    this back, so the dialog is deliberately blunt about that.
    """

    def __init__(self, name: str, password: str, host: str, port: int,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Credentials for {name}")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            CARD_MARGIN_H * 2, CARD_MARGIN_V * 2, CARD_MARGIN_H * 2, CARD_MARGIN_V * 2
        )
        layout.setSpacing(CARD_SPACING * 2)

        title = QLabel(f"Account {name} is ready")
        title.setObjectName("CardTitle")
        layout.addWidget(title)

        warning = QLabel(
            "Copy the password now — it is stored as a hash and cannot be "
            "shown again. If it is lost, reset it to get a new one."
        )
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

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

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


def ask_for_name(parent: QWidget) -> tuple[str, bool, bool]:
    """Prompt for a new account name. Returns (name, full_access, confirmed).

    Validation loops here rather than failing afterwards: being told the rule
    while still in the box beats an error message over a dismissed dialog.
    """
    name = ""
    while True:
        name, confirmed = QInputDialog.getText(
            parent,
            "Add Account",
            "Name for the robot, service or app:\n"
            f"({service.NAME_RULE})",
            QLineEdit.Normal,
            name,
        )
        if not confirmed:
            return "", False, False

        name = name.strip()
        problem = service.validate(name)
        if not problem:
            return name, False, True

        QMessageBox.warning(parent, "That name will not work", problem)
