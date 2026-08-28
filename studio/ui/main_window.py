"""Main window. Assembly only — the pieces live in their own files.

    menus/             one file per menu, each owns its actions
    components/        reusable widgets, incl. BAR 1 and BAR 2
    pages/             one file per screen, each owns its whole slice
    widgets.py         shared card / list builders
    theme.py           palette and stylesheet
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
)

try:
    from .menus import build_menu_bar
    from .menus.view import DEFAULT_ZOOM_INDEX, ZOOM_LEVELS
    from .pages import build_pages
    from .theme import stylesheet
    from .components import ContextBar, NavBar
except ImportError:  # direct script execution
    from menus import build_menu_bar
    from menus.view import DEFAULT_ZOOM_INDEX, ZOOM_LEVELS
    from pages import build_pages
    from theme import stylesheet
    from components import ContextBar, NavBar


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Desk Buddy Studio")
        self.resize(1180, 760)
        self.setStyleSheet(stylesheet())

        self.pages_list = build_pages()

        self._zoom_index = DEFAULT_ZOOM_INDEX
        self._base_point_size = QApplication.font().pointSizeF()
        if self._base_point_size <= 0:
            self._base_point_size = 10.0

        self.menus = build_menu_bar(self)
        self.view_menu = self.menus["View"]
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

        # Qt resets this action's text whenever the dock title changes, so we
        # keep a handle and restore the label on every page switch.
        self.panel_toggle = self.side_dock.toggleViewAction()
        self.panel_toggle.setText("Side Panel")
        self.view_menu.addAction(self.panel_toggle)

    def _build_pages(self) -> None:
        self.pages = QStackedWidget()
        for page in self.pages_list:
            self.pages.addWidget(page.widget())
        self.setCentralWidget(self.pages)

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        self.status_label = QLabel("Ready")
        bar.addWidget(self.status_label)

        self.estop = QPushButton("STOP")
        self.estop.setObjectName("EStop")
        self.estop.setToolTip("Emergency stop — halts the arm")
        bar.addPermanentWidget(self.estop)

    # ---- Zoom -----------------------------------------------------------
    def zoom_in(self) -> None:
        self._set_zoom(self._zoom_index + 1)

    def zoom_out(self) -> None:
        self._set_zoom(self._zoom_index - 1)

    def zoom_reset(self) -> None:
        self._set_zoom(DEFAULT_ZOOM_INDEX)

    def _set_zoom(self, index: int) -> None:
        """Scale the whole UI by resizing the application font.

        Qt propagates the app font to every widget that has not set its own, so
        one change rescales text, buttons, and the layouts built around them.
        """
        index = max(0, min(index, len(ZOOM_LEVELS) - 1))
        if index == self._zoom_index:
            return
        self._zoom_index = index

        scale = ZOOM_LEVELS[index]

        font = QApplication.font()
        font.setPointSizeF(self._base_point_size * scale)
        QApplication.setFont(font)

        # The stylesheet pins font sizes in px, so it has to be rebuilt at the
        # new scale or the app font alone changes nothing.
        self.setStyleSheet(stylesheet(scale))

        # Re-width the current page's side panel.
        side = self.pages_list[self.pages.currentIndex()].side()
        if side is not None:
            side.setFixedWidth(self._side_width())
        self.statusBar().showMessage(f"Zoom {int(scale * 100)}%", 1500)

    @property
    def zoom(self) -> float:
        return ZOOM_LEVELS[self._zoom_index]

    def _side_width(self) -> int:
        """Side panel grows with zoom so its rows never clip."""
        return round(218 * self.zoom)

    def select_page(self, index: int) -> None:
        """The one thing that happens on a BAR 1 click."""
        page = self.pages_list[index]
        self.nav_bar.check(index)
        self.pages.setCurrentIndex(index)
        self.context_bar.show_page(page)
        # The page builds its own side panel; the dock just hosts it.
        side = page.side()
        self.side_dock.setVisible(side is not None)
        if side is not None:
            side.setFixedWidth(self._side_width())
            self.side_dock.setWindowTitle(getattr(side, "title", "") or "Browser")
            self.side_dock.setWidget(side)
        self.panel_toggle.setText("Side Panel")
        self.status_label.setText(page.status)
