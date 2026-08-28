"""Main window. Assembly only — the pieces live in their own files.

    menu_bar.py        the Qt menu
    toolbars.py        BAR 1 and BAR 2
    pages/             one file per screen, each owns its whole slice
    widgets.py         shared card / list builders
    theme.py           palette and stylesheet
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDockWidget,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
)

try:
    from .menu_bar import build_menu_bar
    from .pages import build_pages
    from .theme import STYLESHEET
    from .toolbars import ContextBar, NavBar
    from .widgets import inert_list
except ImportError:  # direct script execution
    from menu_bar import build_menu_bar
    from pages import build_pages
    from theme import STYLESHEET
    from toolbars import ContextBar, NavBar
    from widgets import inert_list


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Desk Buddy Studio")
        self.resize(1180, 760)
        self.setStyleSheet(STYLESHEET)

        self.pages_list = build_pages()

        self.view_menu = build_menu_bar(self)
        self._build_toolbars()
        self._build_side_dock()
        self._build_pages()
        self._build_status_bar()

        self.select_page(0)

    def _build_toolbars(self) -> None:
        self.nav_bar = NavBar(self, self.pages_list, self.select_page)
        self.addToolBar(Qt.TopToolBarArea, self.nav_bar)

        self.context_bar = ContextBar(self)
        self.addToolBarBreak(Qt.TopToolBarArea)
        self.addToolBar(Qt.TopToolBarArea, self.context_bar)

    def _build_side_dock(self) -> None:
        self.side_dock = QDockWidget("Browser", self)
        self.side_dock.setObjectName("SideDock")
        self.side_dock.setAllowedAreas(Qt.LeftDockWidgetArea)
        # Pinned: no floating into its own window, no dragging, no close button.
        # Qt's offscreen/Wayland backends warn about mouse grabs when a dock is
        # dragged, and a panel that pops out is not the layout we want.
        self.side_dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.side_dock)

        toggle = self.side_dock.toggleViewAction()
        toggle.setText("Browser Panel")
        self.view_menu.addAction(toggle)

    def _build_pages(self) -> None:
        self.pages = QStackedWidget()
        for page in self.pages_list:
            self.pages.addWidget(page.page())
        self.setCentralWidget(self.pages)

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        self.status_label = QLabel("Ready")
        bar.addWidget(self.status_label)

        self.estop = QPushButton("STOP")
        self.estop.setObjectName("EStop")
        self.estop.setToolTip("Emergency stop — halts the arm")
        bar.addPermanentWidget(self.estop)

    def select_page(self, index: int) -> None:
        """The one thing that happens on a BAR 1 click."""
        page = self.pages_list[index]
        self.nav_bar.check(index)
        self.pages.setCurrentIndex(index)
        self.context_bar.show_page(page)
        self.side_dock.setWindowTitle(page.side_title or "Browser")
        self.side_dock.setWidget(inert_list(page.side_items))
        self.status_label.setText(page.status)
