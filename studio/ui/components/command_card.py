"""A card that shows a command the user is meant to run themselves.

Separate from Card because the command has to be *selectable* — a QLabel the
user cannot drag-select is useless when the whole point is copying the text.
That difference is worth its own small component rather than a flag on Card:
the call site says which one it wants, instead of decoding a boolean.

Studio never runs these. It shows what it would run, and lets the user decide.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from ..theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING


class CommandCard(QFrame):
    def __init__(
        self,
        title: str,
        body: str,
        command: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, CARD_MARGIN_V
        )
        layout.setSpacing(CARD_SPACING)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("CardTitle")
        layout.addWidget(self.title_label)

        if body:
            self.body_label = QLabel(body)
            self.body_label.setObjectName("CardBody")
            self.body_label.setWordWrap(True)
            layout.addWidget(self.body_label)

        row = QHBoxLayout()
        row.setSpacing(0)

        # A read-only QLineEdit rather than a QLabel: selectable and
        # keyboard-copyable, but not editable — the text is ours, not a field.
        self.command_field = QLineEdit(command)
        self.command_field.setObjectName("CommandText")
        self.command_field.setReadOnly(True)
        self.command_field.setCursorPosition(0)
        row.addWidget(self.command_field)

        self.copy_button = QPushButton("Copy")
        self.copy_button.setObjectName("ContextAction")
        self.copy_button.clicked.connect(self._copy)
        row.addWidget(self.copy_button)

        layout.addLayout(row)

    def _copy(self) -> None:
        self.command_field.selectAll()
        self.command_field.copy()
        self.command_field.setCursorPosition(0)
        self.copy_button.setText("Copied")
