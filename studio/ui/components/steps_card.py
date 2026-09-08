"""A card for a short sequence of commands the user runs themselves.

CommandCard shows one command. This shows several, in order, each with the
caption that says what it does — the shape setup instructions actually take
("install it, then start it, then create an account").

Same rule as CommandCard: Studio shows what to run and never runs it. These
are the steps that need root, which is exactly why they belong to the user.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING


class StepsCard(QFrame):
    def __init__(
        self,
        title: str,
        body: str,
        steps: Iterable[tuple[str, str]],
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

        self.fields: list[QWidget] = []
        for number, (caption, command) in enumerate(steps, start=1):
            label = QLabel(f"{number}. {caption}")
            label.setObjectName("CardBody")
            label.setWordWrap(True)
            layout.addWidget(label)

            row = QHBoxLayout()
            row.setSpacing(0)

            # Read-only rather than a QLabel, for the same reason as
            # CommandCard: the text exists to be selected and copied.
            #
            # A command with newlines in it — a heredoc that writes a config
            # file — gets a plain text edit instead. QLineEdit holds the
            # whole string and copies it correctly, but renders it as one
            # cramped line, so what the user reads before pasting would not
            # be what they are pasting.
            if "\n" in command:
                field = QPlainTextEdit(command)
                field.setReadOnly(True)
                field.setLineWrapMode(QPlainTextEdit.NoWrap)
                # Sized to the text: this is a step to read, not a pane to
                # scroll, and the card should grow rather than box it in.
                metrics = field.fontMetrics()
                lines = command.count("\n") + 1
                field.setFixedHeight(
                    metrics.lineSpacing() * lines + 2 * CARD_SPACING
                )
            else:
                field = QLineEdit(command)
                field.setCursorPosition(0)
            field.setObjectName("CommandText")
            row.addWidget(field)
            self.fields.append(field)

            button = QPushButton("Copy")
            button.setObjectName("ToolbarAction")
            button.clicked.connect(
                lambda _checked=False, f=field, b=button: self._copy(f, b)
            )
            row.addWidget(button)
            layout.addLayout(row)

    def _copy(self, field: QWidget, button: QPushButton) -> None:
        """Put one whole command on the clipboard, however it is displayed.

        Both widgets copy the same way; only the cursor reset differs, and a
        text edit has no setCursorPosition.
        """
        field.selectAll()
        field.copy()
        if isinstance(field, QLineEdit):
            field.setCursorPosition(0)
        else:
            cursor = field.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            field.setTextCursor(cursor)
        button.setText("Copied")
