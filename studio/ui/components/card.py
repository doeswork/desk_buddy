"""A card. The default block for a page body.

Muted variant is the placeholder: real space, obviously inert.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class Card(QFrame):
    def __init__(self, title: str, body: str = "", parent: QWidget | None = None,
                 *, muted: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("CardMuted" if muted else "Card")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(6)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("CardTitle")
        layout.addWidget(self.title_label)

        if body:
            self.body_label = QLabel(body)
            self.body_label.setObjectName("CardBody")
            self.body_label.setWordWrap(True)
            layout.addWidget(self.body_label)
