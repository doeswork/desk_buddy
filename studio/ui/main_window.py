"""Main window. Assembly only — the pieces live in their own files.

    menus/             one file per menu, each owns its actions
    components/        reusable widgets, incl. BAR 1 and BAR 2
    pages/             one file per screen, each owns its whole slice
    widgets.py         shared card / list builders
    theme/             palettes (light / dark / system) and stylesheet
"""

from __future__ import annotations

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
)

from ..services.network import chip_text, shutdown_broker
from ..storage.user_config import keys
from ..storage.user_config.settings import settings
from .menus import build_menu_bar
from .menus.view import DEFAULT_ZOOM_INDEX, ZOOM_LEVELS
from .pages import build_pages
from .theme import DEFAULT_THEME, available, omarchy, stylesheet
from .components import ContextBar, NavBar


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Desk Buddy Studio")
        self.resize(1180, 760)
        self._settings = settings()

        # Appearance is restored before the first paint, so the window never
        # flashes the default theme on the way to the user's.
        self._theme = self._restored_theme()
        self._zoom_index = self._restored_zoom()
        self.setStyleSheet(stylesheet(self.zoom, self._theme))

        self.pages_list = build_pages()

        self._base_point_size = QApplication.font().pointSizeF()
        if self._base_point_size <= 0:
            self._base_point_size = 10.0
        self._apply_zoom_font()

        self.menus = build_menu_bar(self)
        self.view_menu = self.menus["View"]
        self._build_toolbars()
        self._build_side_dock()
        self._build_pages()
        self._build_status_bar()
        self._watch_system_theme()
        self._watch_broker()

        self.select_page(self._restored_page())
        self._restore_window()

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
            # A page that changes its own state asks the chrome to catch up:
            # its actions and status line may no longer be the ones showing.
            page.on_rebuilt = self._page_changed
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

    # ---- Broker ---------------------------------------------------------
    def _watch_broker(self) -> None:
        """Keep BAR 1's broker chip honest.

        A local TCP connect costs well under a millisecond, so polling is
        cheaper and far simpler than tracking a process we may not have
        started — the broker can also go down without telling us.
        """
        self._broker_timer = QTimer(self)
        self._broker_timer.setInterval(3000)
        self._broker_timer.timeout.connect(self.refresh_broker)
        self._broker_timer.start()
        self.refresh_broker()

    def refresh_broker(self) -> None:
        self.nav_bar.set_broker(chip_text())

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
        self._apply_zoom_font()

        # The stylesheet pins font sizes in px, so it has to be rebuilt at the
        # new scale or the app font alone changes nothing.
        self.setStyleSheet(stylesheet(scale, self._theme))

        # Re-width the current page's side panel.
        side = self.pages_list[self.pages.currentIndex()].side()
        if side is not None:
            side.setFixedWidth(self._side_width())
        self.statusBar().showMessage(f"Zoom {int(scale * 100)}%", 1500)

    # ---- Preferences ----------------------------------------------------
    def _restored_theme(self) -> str:
        """The saved theme, if it still exists on this machine.

        The System skin is only present on Omarchy, so a settings file synced
        from another machine — or a desktop that changed underneath us — can
        name a theme we cannot build. Fall back rather than fail to start.
        """
        name = self._settings.get(keys.THEME)
        return name if name in available() else DEFAULT_THEME

    def _restored_zoom(self) -> int:
        index = self._settings.get(keys.ZOOM_INDEX)
        return index if 0 <= index < len(ZOOM_LEVELS) else DEFAULT_ZOOM_INDEX

    def _restored_page(self) -> int:
        index = self._settings.get(keys.LAST_PAGE)
        return index if 0 <= index < len(self.pages_list) else 0

    def _restore_window(self) -> None:
        """Put the window back where it was.

        Guarded: geometry saved on a monitor that is no longer attached, or by
        a newer version of the app, must not stop the window from opening.
        """
        geometry = self._settings.get(keys.GEOMETRY)
        state = self._settings.get(keys.WINDOW_STATE)
        try:
            if geometry:
                self.restoreGeometry(geometry)
            if state:
                self.restoreState(state)
        except (TypeError, ValueError):
            pass

    def closeEvent(self, event) -> None:
        """Save preferences and stop our broker on the way out."""
        for page in self.pages_list:
            service = getattr(page, "service", None)
            if service is not None and hasattr(service, "close"):
                service.close()
        # A broker Studio started belongs to this session; leaving it running
        # would orphan a process holding a port nothing will ever reclaim.
        shutdown_broker()

        self._settings.set(keys.THEME, self._theme)
        self._settings.set(keys.ZOOM_INDEX, self._zoom_index)
        self._settings.set(keys.LAST_PAGE, self.pages.currentIndex())
        self._settings.set(keys.GEOMETRY, self.saveGeometry())
        self._settings.set(keys.WINDOW_STATE, self.saveState())
        self._settings.sync()
        super().closeEvent(event)

    # ---- Theme ----------------------------------------------------------
    def set_theme(self, name: str) -> None:
        """Swap the palette. Same QSS, rebuilt at the current zoom."""
        palettes = available()
        if name not in palettes or name == self._theme:
            return
        self._theme = name
        self.setStyleSheet(stylesheet(self.zoom, palettes[name]))
        # Keep the menu in sync when the theme is set from elsewhere.
        self.theme_actions[name].setChecked(True)
        self.statusBar().showMessage(f"{palettes[name].label} theme", 1500)

    def _watch_system_theme(self) -> None:
        """Follow the desktop's theme while the System skin is selected.

        Omarchy repoints ~/.local/state/omarchy/current on every `theme set`,
        so watching those paths is all it takes to restyle in place — no hook
        script to install, and nothing to clean up if the app is uninstalled.
        """
        if not omarchy.is_omarchy():
            return

        self._theme_watcher = QFileSystemWatcher(self)
        for path in omarchy.WATCH_PATHS:
            self._theme_watcher.addPath(str(path))
        self._theme_watcher.fileChanged.connect(self._system_theme_changed)

    def _system_theme_changed(self, path: str) -> None:
        # Omarchy replaces these files rather than editing them, which drops
        # the watch, so re-add the path before doing anything else.
        if path not in self._theme_watcher.files():
            self._theme_watcher.addPath(path)

        if self._theme != omarchy.NAME:
            return  # Not following the system right now; nothing to restyle.

        live = omarchy.load()
        if live is None:
            return
        self.setStyleSheet(stylesheet(self.zoom, live))
        self.theme_actions[omarchy.NAME].setText(live.label)
        self.statusBar().showMessage(f"{live.label} theme", 1500)

    @property
    def theme(self) -> str:
        return self._theme

    def _apply_zoom_font(self) -> None:
        """Push the current zoom onto the application font.

        Qt propagates the app font to every widget that has not set its own,
        so this is what actually rescales the layouts. Shared by the zoom
        actions and by startup, which has to apply a restored zoom too.
        """
        font = QApplication.font()
        font.setPointSizeF(self._base_point_size * self.zoom)
        QApplication.setFont(font)

    @property
    def zoom(self) -> float:
        return ZOOM_LEVELS[self._zoom_index]

    def _side_width(self) -> int:
        """Side panel grows with zoom so its rows never clip."""
        return round(218 * self.zoom)

    def _page_changed(self) -> None:
        """The current page rebuilt itself; re-read everything it provides.

        Same work as arriving on the page: its actions, its side panel and its
        status may all have changed. Sharing one path is what stops the dock
        from keeping a panel the page has already replaced.
        """
        self._show_page(self.pages_list[self.pages.currentIndex()])
        self.refresh_broker()

    def select_page(self, index: int) -> None:
        """The one thing that happens on a BAR 1 click."""
        self.nav_bar.check(index)
        self.pages.setCurrentIndex(index)
        self._show_page(self.pages_list[index])

    def _show_page(self, page) -> None:
        """Put a page's chrome on screen: BAR 2, the side dock, the status."""
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
