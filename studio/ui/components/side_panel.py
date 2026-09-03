"""The side panel's list. A workspace builds this in `build_side()`.

Normally it lists the workspace's pages, so it is how you move around inside
one. Rows are inert until the thing behind them exists.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QWidget


class SidePanel(QListWidget):
    def __init__(self, title: str, items: list[str], parent: QWidget | None = None,
                 *, enabled: bool = False) -> None:
        super().__init__(parent)
        self.title = title          # what the list is, for callers that ask
        self.setObjectName("SidePanel")
        # A row that responds to a click should say so before it is clicked.
        # Only when there is something behind the rows: a pointing hand over
        # an inert list is the control lying about what it does.
        if enabled:
            self.setCursor(Qt.PointingHandCursor)

        for text in items:
            item = QListWidgetItem(text)
            if not enabled:
                item.setFlags(Qt.NoItemFlags)
            self.addItem(item)
