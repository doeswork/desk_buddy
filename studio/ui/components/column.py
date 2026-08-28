"""Vertical stack of widgets. What most page bodies are."""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget


class Column(QWidget):
    def __init__(self, *children: QWidget, spacing: int = 16) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(spacing)
        for child in children:
            layout.addWidget(child)
