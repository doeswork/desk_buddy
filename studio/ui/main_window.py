"""Main window. Assembly only — the pieces live in their own files.

    menus/             one file per menu, each owns its actions
    components/        reusable widgets, incl. the workspace bar and the toolbar
    workspaces/        one top-level area each, owning its pages
    pages/             the Page base class
    theme/             palettes (light / dark / system) and stylesheet
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QProcess, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QWidget,
)

from ..models.data import app_errors, mqtt_messages
from ..models.config.prefrences import preferences
from ..services import ErrorReporter
from ..services.network import TrafficRecorder, chip_text
from ..storage import keys
from ..storage.settings import settings
from .menus import build_menu_bar
from .menus.view import DEFAULT_ZOOM_INDEX, ZOOM_LEVELS
from .workspaces import build_workspaces
from .theme import DEFAULT_THEME, available, omarchy, stylesheet
from .components import Toolbar, DebugTray, WorkspaceBar, PreferencesDialog


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Desk Buddy Studio")
        self.resize(1180, 760)
        self._settings = settings()
        self._preferences = preferences()
        self._app_errors = app_errors()
        self._error_reporter = ErrorReporter(self._app_errors)
        self._error_reporter.install()

        # Appearance is restored before the first paint, so the window never
        # flashes the default theme on the way to the user's.
        self._theme = self._restored_theme()
        self._zoom_index = self._restored_zoom()
        self.setStyleSheet(stylesheet(self.zoom, self._theme))

        self.workspaces = build_workspaces()

        self._base_point_size = QApplication.font().pointSizeF()
        if self._base_point_size <= 0:
            self._base_point_size = 10.0
        self._apply_zoom_font()

        self.menus = build_menu_bar(self)
        self.view_menu = self.menus["View"]
        self._build_toolbars()
        self._build_side_dock()
        self._build_debug_dock()
        self._build_workspaces()
        self._build_status_bar()
        self._watch_system_theme()
        self._watch_broker()

        # Restoring a workspace is not a user action. In particular, restoring
        # Network must not bypass a disabled automatic-setup preference. The
        # launch hook below decides whether setup starts; later workspace bar clicks
        # still activate Network explicitly.
        self.select_workspace(self._restored_workspace(), activate=False)
        self.workspace.go_to(self._restored_page())
        self._restore_window()
        self._start_network_broker()

    def _build_toolbars(self) -> None:
        self.workspace_bar = WorkspaceBar(self, self.workspaces, self.select_workspace)
        self.addToolBar(Qt.TopToolBarArea, self.workspace_bar)

        self.toolbar = Toolbar(self)
        self.addToolBarBreak(Qt.TopToolBarArea)
        self.addToolBar(Qt.TopToolBarArea, self.toolbar)

    def _build_side_dock(self) -> None:
        self.side_dock = QDockWidget("Browser", self)
        self.side_dock.setObjectName("SideDock")
        self.side_dock.setAllowedAreas(Qt.LeftDockWidgetArea)
        # Pinned: no floating into its own window, no dragging, no close button.
        # Qt's offscreen/Wayland backends warn about mouse grabs when a dock is
        # dragged, and a panel that pops out is not the layout we want.
        self.side_dock.setFeatures(QDockWidget.NoDockWidgetFeatures)

        # No title bar. Which workspace you are in is already said by the
        # checked tab in the workspace bar, and naming it again above its own page list
        # is a header that carries no information. An empty widget is how Qt
        # removes the bar; setTitleBarWidget(None) restores the default.
        self.side_dock.setTitleBarWidget(QWidget())
        self.addDockWidget(Qt.LeftDockWidgetArea, self.side_dock)

        self.panel_toggle = self.side_dock.toggleViewAction()
        self.panel_toggle.setText("Side Panel")
        self.view_menu.addAction(self.panel_toggle)

    def _build_debug_dock(self) -> None:
        """The old Logs workspace, reshaped as a bottom activity tray."""
        self._mqtt_messages = mqtt_messages()
        self._traffic_recorder = TrafficRecorder(self._mqtt_messages)
        # The Serial Monitor's MQTT dialog fills itself in from Studio's own
        # accounts and the broker it actually reachable on — both live on
        # NetworkWorkspace, which is already built (self.workspaces, above)
        # by the time this runs.
        network = next(w for w in self.workspaces if w.key == "network")
        self.debug_tray = DebugTray(
            self._mqtt_messages,
            self._app_errors,
            self._traffic_recorder,
            lambda: self.debug_dock.setVisible(False),
            broker_host=network.broker_host,
            network=network,
        )

        self.debug_dock = QDockWidget("Debug Tray", self)
        self.debug_dock.setObjectName("DebugDock")
        self.debug_dock.setAllowedAreas(Qt.BottomDockWidgetArea)
        # Closable keeps Qt's toggleViewAction enabled. The empty title bar
        # removes the floating/close chrome; View → Debug Tray is the control.
        self.debug_dock.setFeatures(QDockWidget.DockWidgetClosable)
        self.debug_dock.setTitleBarWidget(QWidget())
        self.debug_dock.setMinimumHeight(220)
        self.debug_dock.setWidget(self.debug_tray)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.debug_dock)
        self.debug_dock.setVisible(False)
        self.debug_dock.visibilityChanged.connect(
            lambda visible: self.debug_tray.refresh(force=True) if visible else None
        )

        self.debug_toggle = self.debug_dock.toggleViewAction()
        self.debug_toggle.setText("Debug Tray")
        self.debug_toggle.setShortcut("Ctrl+`")
        self.view_menu.addAction(self.debug_toggle)

    def _build_workspaces(self) -> None:
        self.stack = QStackedWidget()
        for workspace in self.workspaces:
            # A workspace that changes its own state — or whose page did —
            # asks the chrome to catch up: its actions, its side panel and its
            # status line may no longer be the ones showing.
            workspace.on_rebuilt = self._workspace_changed
            workspace.announce = self._announce
            self.stack.addWidget(workspace.widget())
        self.setCentralWidget(self.stack)

    def _announce(self, message: str) -> None:
        """One transient line in the status strip, for a finished action.

        `showMessage` paints over `status_label` rather than replacing its
        text, so a rebuild that resets the label — which every refresh does —
        leaves this standing until it times out on its own.
        """
        self.statusBar().showMessage(message, 6000)

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
        """Keep the workspace bar's broker chip honest.

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
        network = next((workspace for workspace in self.workspaces if workspace.key == "network"), None)
        if network is not None:
            health = network.broker_health()
            self.workspace_bar.set_broker(health.chip)
            self.workspace_bar.set_issue("network", health.issue, health.tooltip)
            network.set_issue("broker", health.issue, health.tooltip)
        else:
            self.workspace_bar.set_broker(chip_text())
        self._traffic_recorder.reconcile()
        vision = next((workspace for workspace in self.workspaces if workspace.key == "vision"), None)
        if vision is not None:
            vision.start_enabled()

    def _start_network_broker(self) -> None:
        """Schedule automatic broker setup after the first window paint.

        The timer fires only once Qt's event loop is running, so a package
        install or authorization prompt never appears before Studio itself.
        Network's coordinator deduplicates this against a quick user click.
        """
        if not self._preferences.mqtt_broker_auto_start:
            return
        network = next(
            workspace for workspace in self.workspaces
            if workspace.key == "network"
        )
        QTimer.singleShot(0, network.activate)

    def open_preferences(self) -> None:
        PreferencesDialog(self._preferences, self).exec()

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

        # Re-width the current workspace's side panel.
        side = self.workspace.side()
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

    def _restored_workspace(self) -> int:
        index = self._settings.get(keys.LAST_WORKSPACE)
        return index if 0 <= index < len(self.workspaces) else 0

    def _restored_page(self) -> str:
        """The page key within the restored workspace.

        A key rather than an index: pages get added and reordered between
        releases, and a stale index would silently restore a different screen
        than the one the user left. A key that no longer exists just falls
        back to the workspace's first page.
        """
        return self._settings.get(keys.LAST_PAGE)

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

        # The two bars are stacked, never side by side, whatever the saved
        # state says. `restoreState` matches toolbars by object name, so a
        # state written before either was renamed restores neither — and the
        # break between them is lost with them, dropping the toolbar up onto
        # the workspace bar's row. Re-asserting it here costs nothing when
        # the state is current and is the whole fix when it is not.
        self.insertToolBarBreak(self.toolbar)

    def closeEvent(self, event) -> None:
        """Save preferences and drop the broker connection on the way out.

        No broker is stopped: Studio does not start one. The machine's
        Mosquitto outlives the app, which is the point of using it.
        """
        if any(getattr(workspace, "setup", None) and workspace.setup.running for workspace in self.workspaces):
            self.statusBar().showMessage("Broker setup is still running. Finish the system authorization prompt before closing.", 8000)
            event.ignore()
            return
        vision = next((workspace for workspace in self.workspaces if workspace.key == "vision"), None)
        if vision is not None and vision.install_running:
            answer = QMessageBox.question(
                self,
                "Cancel Vision installation?",
                "A model is still downloading. Cancel it and close Studio?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        for workspace in self.workspaces:
            shutdown = getattr(workspace, "shutdown", None)
            if shutdown is not None:
                shutdown()
        self.debug_tray.shutdown()
        self._traffic_recorder.stop()

        self._settings.set(keys.THEME, self._theme)
        self._settings.set(keys.ZOOM_INDEX, self._zoom_index)
        self._settings.set(keys.LAST_WORKSPACE, self.stack.currentIndex())
        self._settings.set(keys.LAST_PAGE, self.workspace.page_key)
        self._settings.set(keys.GEOMETRY, self.saveGeometry())
        self._settings.set(keys.WINDOW_STATE, self.saveState())
        self._settings.sync()
        self._error_reporter.uninstall()
        super().closeEvent(event)

    def restart_app(self) -> bool:
        """Start a replacement that waits for this process to release its lock."""
        from ..app import RESTART_WAIT_ARGUMENT

        answer = QMessageBox.question(
            self,
            "Restart Desk Buddy Studio?",
            "Studio will close and reopen. The broker will keep running while "
            "Studio's connections and services restart cleanly.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return False

        if getattr(sys, "frozen", False):
            program = sys.executable
            arguments = [RESTART_WAIT_ARGUMENT, *sys.argv[1:]]
            working_directory = str(Path(sys.executable).resolve().parent)
        else:
            program = sys.executable
            arguments = ["-m", "studio", RESTART_WAIT_ARGUMENT, *sys.argv[1:]]
            working_directory = str(Path(__file__).resolve().parents[2])

        result = QProcess.startDetached(program, arguments, working_directory)
        started = result[0] if isinstance(result, tuple) else result
        if not started:
            QMessageBox.warning(
                self,
                "Could not restart",
                "Studio could not start the replacement process.",
            )
            return False

        self.close()
        return True

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

    @property
    def workspace(self):
        """The workspace showing right now."""
        return self.workspaces[self.stack.currentIndex()]

    def _workspace_changed(self) -> None:
        """The current workspace rebuilt itself; re-read what it provides.

        Same work as arriving on it: its actions, its side panel and its
        status may all have changed. Sharing one path is what stops the dock
        from keeping a panel the workspace has already replaced.
        """
        self._show_workspace(self.workspace)
        self.refresh_broker()

    def select_workspace(self, index: int, *, activate: bool = True) -> None:
        """The one thing that happens on a workspace bar click."""
        self.workspace_bar.check(index)
        self.stack.setCurrentIndex(index)
        self._show_workspace(self.workspaces[index])
        workspace = self.workspaces[index]
        if activate and hasattr(workspace, "activate"):
            # Setup is an activation effect, never a hidden-widget build effect.
            QTimer.singleShot(0, workspace.activate)

    def _show_workspace(self, workspace) -> None:
        """Put a workspace's chrome on screen: the toolbar, the dock, the status.

        the toolbar and the status come from the page showing inside it; the side
        panel is the workspace's own list of pages.
        """
        self.toolbar.show_page(workspace)

        # The workspace builds its own side panel; the dock just hosts it.
        side = workspace.side()
        self.side_dock.setVisible(side is not None)
        if side is not None:
            side.setFixedWidth(self._side_width())
            self.side_dock.setWidget(side)
        health = getattr(workspace, "_last_health", None)
        if health is not None:
            if hasattr(self, "workspace_bar"):
                self.workspace_bar.set_issue("network", health.issue, health.tooltip)
            workspace.set_issue("broker", health.issue, health.tooltip)
        self.status_label.setText(workspace.status)
