"""Shared widget builders. Every page uses these.

Keeps the page files declarative: they say what goes on the page, not how a
card is assembled.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
)


def not_built_badge() -> QLabel:
    badge = QLabel("NOT BUILT YET")
    badge.setObjectName("NotBuilt")
    badge.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
    return badge


def card(title: str, body: str) -> QFrame:
    """A muted placeholder card. Real space, obviously inert."""
    frame = QFrame()
    frame.setObjectName("CardMuted")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(6)

    heading = QLabel(title)
    heading.setObjectName("CardTitle")
    text = QLabel(body)
    text.setObjectName("CardBody")
    text.setWordWrap(True)

    layout.addWidget(heading)
    layout.addWidget(text)
    return frame


def inert_list(items: list[str], width: int = 218) -> QListWidget:
    """Side-panel list whose rows are not selectable until the thing behind them exists."""
    widget = QListWidget()
    widget.setFixedWidth(width)
    for text in items:
        item = QListWidgetItem(text)
        item.setFlags(Qt.NoItemFlags)
        widget.addItem(item)
    return widget
