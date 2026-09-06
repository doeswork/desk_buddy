"""Invisible stretch. Pushes whatever follows it to the right edge."""

from __future__ import annotations

from PySide6.QtWidgets import QSizePolicy, QWidget


def spacer() -> QWidget:
    widget = QWidget()
    widget.setObjectName("Spacer")
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return widget
