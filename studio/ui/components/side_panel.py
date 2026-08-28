"""The side panel's list. A page builds this itself in `build_side()`.

Rows are inert until the thing behind them exists.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QWidget


class SidePanel(QListWidget):
    def __init__(self, title: str, items: list[str], parent: QWidget | None = None,
                 *, enabled: bool = False) -> None:
        super().__init__(parent)
        self.title = title          # the dock reads this for its header
        self.setObjectName("SidePanel")

        for text in items:
            item = QListWidgetItem(text)
            if not enabled:
                item.setFlags(Qt.NoItemFlags)
            self.addItem(item)
