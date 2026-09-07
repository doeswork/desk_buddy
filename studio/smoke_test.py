"""Headless check that the UI builds. Runs in CI before the slow bundle step.

    QT_QPA_PLATFORM=offscreen python -m studio.smoke_test
"""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox, QPlainTextEdit

from .storage import keys
from .storage.settings import Settings
from .ui.components import ActionSpec
from .ui.components.serial_monitor import MqttDialog, WifiDialog
from .ui.main_window import MainWindow
from .ui.menus.view import DEFAULT_ZOOM_INDEX


def main() -> int:
    app = QApplication([])
    # NetworkWorkspace deliberately invalidates persisted WSL endpoints until
    # it can inspect Windows again. The smoke test builds that real workspace,
    # so give every Network settings reader a disposable backend rather than
    # clearing the developer's verified endpoint merely by running tests.
    settings_directory = tempfile.TemporaryDirectory()
    smoke_settings = Settings(QSettings(
        os.path.join(settings_directory.name, "settings.ini"),
        QSettings.IniFormat,
    ))
    settings_patches = [
        mock.patch(
            "studio.ui.workspaces.network.workspace.settings",
            return_value=smoke_settings,
        ),
        mock.patch(
            "studio.storage.settings.settings", return_value=smoke_settings
        ),
        mock.patch(
            "studio.services.network.broker.system.settings",
            return_value=smoke_settings,
        ),
    ]
    for patcher in settings_patches:
        patcher.start()
    # The smoke test builds every page and pumps Qt events. Never launch an
    # authorization prompt on the developer/CI host, including restored Network.
    activation_patch = mock.patch(
        "studio.ui.workspaces.network.workspace.NetworkWorkspace.activate"
    )
    activate_network = activation_patch.start()
    # Setup is scheduled after construction so the main window exists before
    # any authorization prompt. The operation itself stays mocked: smoke tests
    # must never touch the developer/CI machine's broker.
    with (
        mock.patch(
            "studio.services.network.broker.system.create_account"
        ) as create_account,
        mock.patch("studio.ui.main_window.preferences") as preferences,
    ):
        preferences.return_value.mqtt_broker_auto_start = True
        window = MainWindow()
    create_account.assert_not_called()
    activate_network.assert_not_called()
    window.show()
    app.processEvents()
    activate_network.assert_called_once_with()
    activate_network.reset_mock()

    # Disabling launch-time automation does not remove the explicit Network
    # entry point. A later visit still asks that workspace to prepare MQTT.
    window._preferences.mqtt_broker_auto_start = False
    network_index = next(
        index for index, workspace in enumerate(window.workspaces)
        if workspace.key == "network"
    )
    with mock.patch.object(QTimer, "singleShot") as single_shot:
        window._start_network_broker()
        single_shot.assert_not_called()
    window.select_workspace(network_index)
    app.processEvents()
    activate_network.assert_called_once_with()
    activate_network.reset_mock()

    assert window.windowTitle() == "Desk Buddy Studio"

    workspaces = window.workspaces
    assert len(workspaces) == 5, f"expected 5 workspaces, got {len(workspaces)}"
    assert window.stack.count() == len(workspaces), "workspace missing"
    assert "logs" not in [workspace.key for workspace in workspaces]
    check_theme_menu(window)
    check_debug_tray(window)

    total_pages = 0
    for index, workspace in enumerate(workspaces):
        window.select_workspace(index)

        assert window.stack.currentIndex() == index, (
            f"{workspace.key}: workspace did not switch"
        )
        assert window.nav_bar.actions_by_index[index].isChecked(), (
            f"{workspace.key}: not checked"
        )

        # The panel lists links, not pages: it shows what the workspace chose
        # to link to, and disappears when there is nothing to choose between.
        links = workspace.links()
        side = workspace.side()
        if len(links) > 1:
            assert side is not None, f"{workspace.key}: no side panel"
            assert side.count() == len(links), (
                f"{workspace.key}: panel rows {side.count()} != links {len(links)}"
            )
        for key, _label in links:
            assert workspace.find(key) is not None, (
                f"{workspace.key}: link {key!r} points at no page"
            )

        # BAR 2 belongs to the workspace: the same buttons in the same order
        # on every one of its pages, whatever else changes.
        expected = None
        for page in workspace.pages:
            workspace.go_to(page.key)
            total_pages += 1

            assert workspace.page is page, f"{page.key}: page did not switch"
            check_actions(window, workspace, page)

            labels = list(window.context_bar.buttons)
            if expected is None:
                expected = labels
            assert labels == expected, (
                f"{workspace.key}: bar changed on {page.key}: "
                f"{labels} != {expected}"
            )

        # A page the panel does not link to must leave no row lit: the user is
        # somewhere none of the links point.
        unlinked = [p for p in workspace.pages
                    if p.key not in [k for k, _ in links]]
        if unlinked and side is not None:
            workspace.go_to(unlinked[0].key)
            assert side.currentRow() == -1, (
                f"{workspace.key}: {unlinked[0].key} lit a link row"
            )
        workspace.go_to(workspace.pages[0].key)

    check_persistence(window)
    check_diagnostics(window)

    print(f"OK: {len(workspaces)} workspaces / {total_pages} pages, "
          f"two bars swap, preferences round-trip, diagnostics render")
    for patcher in reversed(settings_patches):
        patcher.stop()
    settings_directory.cleanup()
    app.quit()
    return 0


def check_actions(window, workspace, page) -> None:
    """BAR 2 shows exactly what the workspace declared, on every page."""
    specs = [s for s in workspace.build_actions() if isinstance(s, ActionSpec)]
    labels = list(window.context_bar.buttons)
    expected = [s.label for s in specs]
    assert labels == expected, f"{page.key}: context bar {labels} != {expected}"

    # A button is clickable exactly when its action has something to do.
    # An enabled button with no handler is a control that lies.
    by_label = {s.label: s for s in specs}
    for label, button in window.context_bar.buttons.items():
        spec = by_label[label]
        assert button.isEnabled() == spec.clickable, (
            f"{page.key}: {label} enabled={button.isEnabled()} "
            f"but clickable={spec.clickable}"
        )
        if button.isEnabled():
            assert spec.on_click is not None, (
                f"{page.key}: {label} is enabled with no handler"
            )

    # At most one primary, matching what the page declared.
    primaries = [l for l, b in window.context_bar.buttons.items()
                 if b.objectName() == "ContextPrimary"]
    declared = [s.label for s in workspace.build_actions()
                if isinstance(s, ActionSpec) and s.primary]
    assert primaries == declared, f"{page.key}: primary {primaries} != {declared}"
    assert len(primaries) <= 1, f"{page.key}: {len(primaries)} primaries"


def check_theme_menu(window) -> None:
    """Bundled themes stay reachable without filling the View dropdown."""
    bundled = {
        name for name in window.theme_actions if name.startswith("omarchy-")
    }
    submenu = next(
        (action.menu() for action in window.view_menu.actions()
         if action.text() == "Omarchy Themes"),
        None,
    )
    assert submenu is not None, "View has no Omarchy Themes submenu"

    nested = {
        name for name, action in window.theme_actions.items()
        if action in submenu.actions()
    }
    assert nested == bundled, f"nested themes {nested} != bundled {bundled}"
    assert not any(
        window.theme_actions[name] in window.view_menu.actions()
        for name in bundled
    ), "a bundled Omarchy theme leaked into the top-level View menu"


def check_debug_tray(window) -> None:
    """The activity log is a bottom tray, not a sixth workspace."""
    assert window.debug_dock.allowedAreas() == Qt.BottomDockWidgetArea
    assert window.debug_toggle.text() == "Debug Tray"
    assert window.debug_tray.tabs.count() == 4
    assert [window.debug_tray.tabs.tabText(index) for index in range(4)] == [
        "MQTT Activity",
        "App Errors",
        "Flash Firmware",
        "Serial Monitor",
    ]
    flash = window.debug_tray.firmware_tab
    assert flash.board_label.text() == "ESP32-S3-CAM (N16R8)"
    assert flash.port.isEditable()
    assert flash.flash_button.text() == "Flash Firmware"
    assert not flash.flash_button.isEnabled()
    assert not flash.port_requirement.isHidden()
    flash.port.addItem("/dev/ttyUSB0 — USB serial", "/dev/ttyUSB0")
    assert flash.flash_button.isEnabled()
    assert flash.port_requirement.isHidden()
    assert flash.output.isReadOnly()
    assert flash.output.lineWrapMode() == QPlainTextEdit.NoWrap
    flash.wrap_lines.click()
    assert flash.output.lineWrapMode() == QPlainTextEdit.WidgetWidth
    serial = window.debug_tray.serial_tab
    assert "115200" in serial.baud.currentText()
    assert serial.port.isEditable()
    assert serial.connect_button.text() == "Connect"
    assert serial.output.isReadOnly()
    assert serial.output.lineWrapMode() == QPlainTextEdit.NoWrap
    serial.wrap_lines.click()
    assert serial.output.lineWrapMode() == QPlainTextEdit.WidgetWidth
    assert serial.wifi_button.text() == "Set Wi-Fi…"

    assert window.debug_tray.output.lineWrapMode() == QPlainTextEdit.NoWrap
    window.debug_tray.message_wrap.click()
    assert window.debug_tray.output.lineWrapMode() == QPlainTextEdit.WidgetWidth
    assert window.debug_tray.error_output.lineWrapMode() == QPlainTextEdit.NoWrap
    window.debug_tray.error_wrap.click()
    assert window.debug_tray.error_output.lineWrapMode() == QPlainTextEdit.WidgetWidth

    wifi_dialog = WifiDialog(serial, on_save=lambda _ssid, _password: None)
    assert wifi_dialog.password.echoMode() == QLineEdit.Password
    assert wifi_dialog.password_toggle.text() == ""
    assert wifi_dialog.password_toggle.cursor().shape() == Qt.PointingHandCursor
    assert wifi_dialog.password_toggle.toolTip() == "Show password"
    wifi_dialog.password_toggle.click()
    assert wifi_dialog.password.echoMode() == QLineEdit.Normal
    assert wifi_dialog.password_toggle.toolTip() == "Hide password"
    wifi_dialog.password_toggle.click()
    assert wifi_dialog.password.echoMode() == QLineEdit.Password

    mqtt_dialog = MqttDialog(
        serial,
        accounts=[SimpleNamespace(name="robot-1", password="secret")],
        broker_host="192.168.1.2",
        broker_port=1883,
        on_save=lambda *_args: None,
    )
    assert mqtt_dialog.password_toggle.isEnabled()
    mqtt_dialog.password_toggle.click()
    assert mqtt_dialog.password.echoMode() == QLineEdit.Normal
    assert window.debug_tray.close_button.text() == "×"
    assert window.restart_action.text() == "Restart App…"
    assert window.preferences_action.text() == "Preferences…"
    assert window.preferences_action.isEnabled()

    # Saying No must never spawn a process or close the current window.
    with (
        mock.patch(
            "studio.ui.main_window.QMessageBox.question",
            return_value=QMessageBox.No,
        ) as question,
        mock.patch("studio.ui.main_window.QProcess.startDetached") as start,
    ):
        window.restart_action.trigger()
        assert question.call_count == 1
        start.assert_not_called()

    # Saying Yes launches a replacement before closing this process. Patch
    # both edges so the smoke test proves the order without restarting itself.
    with (
        mock.patch(
            "studio.ui.main_window.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ),
        mock.patch(
            "studio.ui.main_window.QProcess.startDetached",
            return_value=(True, 12345),
        ) as start,
        mock.patch.object(window, "close") as close,
    ):
        assert window.restart_app()
        start.assert_called_once()
        close.assert_called_once()

    if window.debug_dock.isVisible():
        window.debug_toggle.trigger()
    assert not window.debug_dock.isVisible()
    window.debug_toggle.trigger()
    assert window.debug_dock.isVisible()
    window.debug_toggle.trigger()
    assert not window.debug_dock.isVisible()


def check_diagnostics(window) -> None:
    """The report must never be the thing that breaks when something breaks."""
    from .diagnostics import text

    report = text(window)
    for heading in ("# Desk Buddy Studio", "## Environment", "## Broker",
                    "## Paths", "## Window"):
        assert heading in report, f"diagnostics missing {heading!r}"
    assert "FAILED:" not in report, f"a diagnostics section raised:\n{report}"


def check_persistence(window) -> None:
    """Preferences a user changed must come back on the next launch.

    Runs against a throwaway settings file so a CI box — or the developer's own
    machine — never has its real preferences rewritten by the test.
    """
    path = tempfile.mktemp(suffix=".ini")
    window._settings = Settings(QSettings(path, QSettings.IniFormat))

    # The window restored the developer's real zoom on the way up, so what
    # zoom_in() lands on is relative to that — not to the default. Reset
    # first and the expected value is a constant again.
    window.zoom_reset()

    # Calibration, and its third step: workspace index and page key both have
    # to come back, so the workspace under test is one with several pages.
    calibration_index = next(
        index for index, workspace in enumerate(window.workspaces)
        if workspace.key == "calibration"
    )
    window.select_workspace(calibration_index)
    window.workspace.go_to("visual")
    window.zoom_in()
    window.set_theme("dark")
    window.close()          # closeEvent is what saves

    saved = Settings(QSettings(path, QSettings.IniFormat))
    assert saved.get(keys.THEME) == "dark", saved.get(keys.THEME)
    assert saved.get(keys.LAST_WORKSPACE) == calibration_index, (
        saved.get(keys.LAST_WORKSPACE)
    )
    assert saved.get(keys.LAST_PAGE) == "visual", saved.get(keys.LAST_PAGE)
    assert saved.get(keys.ZOOM_INDEX) == DEFAULT_ZOOM_INDEX + 1, (
        saved.get(keys.ZOOM_INDEX)
    )
    assert saved.get(keys.GEOMETRY), "window geometry not saved"

    os.unlink(path)


if __name__ == "__main__":
    raise SystemExit(main())
