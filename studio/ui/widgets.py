"""Small shared helpers that are not full components."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QSizePolicy


def not_built_badge() -> QLabel:
    """The NOT BUILT YET chip beside a page title."""
    badge = QLabel("NOT BUILT YET")
    badge.setObjectName("NotBuilt")
    badge.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
    return badge
