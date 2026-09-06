"""Offscreen broker setup UI tests; all system operations are mocked.

    QT_QPA_PLATFORM=offscreen python -m studio.ui.workspaces.network.tests
"""
from __future__ import annotations

import contextlib
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from ....storage import keys
from ....storage.settings import Settings
from ....services.network.broker import system
from ....services.network.broker.setup import Coordinator, SetupStatus
from .workspace import NetworkWorkspace

_app = QApplication.instance() or QApplication([])


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.store = Settings(QSettings(str(root / "settings.ini"), QSettings.IniFormat))
        self.stack.enter_context(mock.patch("studio.ui.workspaces.network.workspace.settings", return_value=self.store))
        self.stack.enter_context(mock.patch("studio.storage.settings.settings", return_value=self.store))
        self.stack.enter_context(mock.patch.object(system, "_record_password"))
        self.stack.enter_context(mock.patch("studio.services.network.pub_sub.client.mqtt_client"))
        self.stack.enter_context(mock.patch.object(system, "can_grant", return_value=True))
        self.stack.enter_context(mock.patch.object(system, "write_access", return_value=system.WriteAccess(True, True, True, "/usr/bin/mosquitto_passwd", True)))
        self.space = NetworkWorkspace()
        self.addCleanup(self.cleanup_timer)
        self.space.refresh = mock.Mock()

    def cleanup_timer(self):
        if self.space._setup_timer is not None:
            self.space._setup_timer.stop()

    def widget(self, state=None):
        if state is not None:
            self.space.setup.status = state
        page = self.space.find("broker")
        page._widget = None
        return page.widget()

    def test_hidden_page_build_does_not_start_setup_or_generate_credentials(self):
        with mock.patch.object(self.space.setup, "start") as start, mock.patch.object(system, "suggested_account") as suggest:
            self.widget()
        start.assert_not_called()
        suggest.assert_not_called()

    def test_commands_are_collapsed_for_every_state(self):
        for state in ("idle", "checking", "installing", "configuring", "starting", "verifying", "ready", "failed", "cancelled"):
            widget = self.widget(SetupStatus(state, "A status message", detail="A useful reason"))
            content = widget.findChild(QWidget, "ManualSetupContent")
            self.assertTrue(content.isHidden())
            self.assertFalse(widget.findChildren(QLabel, "CommandText"))
            self.assertFalse(any(button.text() == "Copy" for button in widget.findChildren(QPushButton)))

    def test_expanding_manual_setup_exposes_commands(self):
        ready = SetupStatus(
            "ready", "Connected", connected=True,
            robot_host="192.168.1.2", network_ready=True,
        )
        with mock.patch("studio.ui.workspaces.network.broker.firewall_card", return_value=None):
            widget = self.widget(ready)
            toggle = next(button for button in widget.findChildren(QPushButton) if button.text() == "Advanced / Manual setup")
            toggle.click()
        self.assertFalse(widget.findChild(QWidget, "ManualSetupContent").isHidden())
        self.assertTrue(any(button.text() == "Copy" for button in widget.findChildren(QPushButton)))
        self.assertTrue(any(label.text() == "Revoke" for label in widget.findChildren(QLabel)))
        toggle.click()
        self.assertTrue(widget.findChild(QWidget, "ManualSetupContent").isHidden())

    def test_permission_controls_are_hidden_with_manual_setup(self):
        ready = SetupStatus(
            "ready", "Connected", connected=True,
            robot_host="192.168.1.2", network_ready=True,
        )
        widget = self.widget(ready)
        self.assertFalse(any(label.text() == "Revoke" for label in widget.findChildren(QLabel)))

    def test_retry_is_only_offered_after_failure_or_cancellation(self):
        for state in ("idle", "checking", "ready", "failed", "cancelled"):
            widget = self.widget(SetupStatus(state, "Status"))
            buttons = [button.text() for button in widget.findChildren(QPushButton)]
            self.assertEqual("Retry setup" in buttons, state in ("failed", "cancelled"))

    def test_network_activation_starts_once_and_stays_responsive(self):
        release = threading.Event()
        self.addCleanup(release.set)
        def operation(request, emit):
            emit("installing", "Installing…")
            release.wait(2)
            return SetupStatus("ready", "Connected", True, "192.168.1.2", True, user=request["user"])
        self.space.setup = Coordinator(operation)
        self.space.activate()
        self.assertTrue(self.space.setup.running)
        timer = self.space._setup_timer
        self.space.activate()
        self.assertIs(timer, self.space._setup_timer)
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        _app.processEvents()
        self.assertTrue(ticks)
        self.space._poll_setup()
        self.assertEqual(self.space.setup.status.state, "installing")
        release.set()
        deadline = time.monotonic() + 2
        while self.space.setup.running and time.monotonic() < deadline:
            self.space._poll_setup()
            time.sleep(0.005)
        self.assertTrue(self.store.get(keys.SYSTEM_BROKER_VERIFIED))
        self.assertEqual(self.store.get(keys.SYSTEM_BROKER_ROBOT_HOST), "192.168.1.2")
        self.space.refresh.assert_called_once()

    def test_failure_and_revoke_pause_future_automatic_attempts(self):
        self.store.set(keys.SYSTEM_BROKER_SETUP_PAUSED, True)
        with mock.patch.object(self.space.setup, "start") as start:
            self.space.activate()
        start.assert_not_called()
        with mock.patch.object(self.space.setup, "start", return_value=False):
            self.space.revoke_access()
        self.assertTrue(self.store.get(keys.SYSTEM_BROKER_SETUP_PAUSED))
        self.assertTrue(self.space.setup.paused)

    def test_current_live_result_replaces_saved_verification(self):
        self.store.set(keys.SYSTEM_BROKER_VERIFIED, True)
        release = threading.Event()
        self.addCleanup(release.set)
        def operation(request, emit):
            release.wait(2)
            return SetupStatus("failed", "Broker is down")
        self.space.setup = Coordinator(operation)
        self.space.activate()
        self.assertFalse(self.store.get(keys.SYSTEM_BROKER_VERIFIED))
        release.set()
        deadline = time.monotonic() + 2
        while self.space.setup.running and time.monotonic() < deadline:
            self.space._poll_setup()
            time.sleep(0.005)
        self.assertTrue(self.store.get(keys.SYSTEM_BROKER_SETUP_PAUSED))

    def test_robot_status_does_not_claim_an_external_connection(self):
        widget = self.widget(SetupStatus("ready", "Connected", True, "192.168.1.2", True))
        labels = [label.text() for label in widget.findChildren(QLabel)]
        self.assertTrue(any("robot connection not yet confirmed" in text for text in labels))

    def test_local_broker_is_ready_with_forwarding_guidance_only_in_advanced(self):
        state = SetupStatus(
            "ready", "Connected as “studio” at 127.0.0.1:1883.", connected=True,
            detail="Broker address inside WSL: 172.20.0.2:1883. Windows forwarding is managed separately.",
        )
        widget = self.widget(state)
        labels = [label.text() for label in widget.findChildren(QLabel)]
        self.assertIn("Studio is connected", labels)
        self.assertFalse(any("incomplete" in text or "needs attention" in text for text in labels))
        self.assertFalse(any("172.20.0.2" in text for text in labels))
        self.assertFalse(any(button.text() == "Retry setup" for button in widget.findChildren(QPushButton)))
        with mock.patch("studio.ui.workspaces.network.broker.firewall_card", return_value=None):
            next(button for button in widget.findChildren(QPushButton) if button.text() == "Advanced / Manual setup").click()
        self.assertIn(state.detail, [label.text() for label in widget.findChildren(QLabel)])

    def test_retry_clears_old_windows_failure_pause_after_local_success(self):
        self.store.set(keys.SYSTEM_BROKER_SETUP_PAUSED, True)
        self.store.set(keys.SYSTEM_BROKER_USER, "studio-2")
        self.store.set(keys.SYSTEM_BROKER_PASSWORD, "existing-password")
        self.store.set(keys.SYSTEM_BROKER_ROBOT_HOST, "192.168.1.10")
        operation = mock.Mock(return_value=SetupStatus(
            "ready", "Connected locally", connected=True, user="studio-2",
        ))
        self.space.setup = Coordinator(operation)
        self.space.activate()
        operation.assert_not_called()
        widget = self.widget()
        next(button for button in widget.findChildren(QPushButton) if button.text() == "Retry setup").click()
        deadline = time.monotonic() + 2
        while self.space.setup.running and time.monotonic() < deadline:
            self.space._poll_setup()
            time.sleep(0.005)
        self.assertFalse(self.space.setup.running)
        self.assertEqual(self.space.setup.status.state, "ready")
        self.assertFalse(self.store.get(keys.SYSTEM_BROKER_SETUP_PAUSED))
        self.assertTrue(self.store.get(keys.SYSTEM_BROKER_VERIFIED))
        self.assertFalse(self.store.get(keys.SYSTEM_BROKER_NETWORK_READY))
        self.assertEqual(self.store.get(keys.SYSTEM_BROKER_ROBOT_HOST), "")
        self.assertEqual(self.store.get(keys.SYSTEM_BROKER_PASSWORD), "existing-password")
        system._record_password.assert_called_once_with("studio-2", "existing-password")

    def test_workspace_bar_remains_empty(self):
        self.assertEqual(self.space.build_actions(), [])

    def test_window_selection_activates_network_but_repaint_does_not(self):
        from ...main_window import MainWindow
        window = SimpleNamespace(nav_bar=mock.Mock(), stack=mock.Mock(), workspaces=[self.space], _show_workspace=mock.Mock())
        with mock.patch("studio.ui.main_window.QTimer.singleShot") as schedule:
            MainWindow.select_workspace(window, 0)
        self.assertEqual(schedule.call_args.args[1], self.space.activate)
        with mock.patch("studio.ui.main_window.QTimer.singleShot") as schedule:
            MainWindow.select_workspace(window, 0, activate=False)
        schedule.assert_not_called()

    def test_startup_hook_respects_the_existing_preference(self):
        from ...main_window import MainWindow
        restored = SimpleNamespace(key="control", activate=mock.Mock())
        window = SimpleNamespace(
            _preferences=SimpleNamespace(mqtt_broker_auto_start=False),
            workspaces=[restored, self.space],
            # A restored non-Network selection must not affect the launch hook.
            stack=SimpleNamespace(currentIndex=lambda: 0),
        )
        with mock.patch("studio.ui.main_window.QTimer.singleShot") as schedule:
            MainWindow._start_network_broker(window)
        schedule.assert_not_called()
        window._preferences.mqtt_broker_auto_start = True
        with mock.patch("studio.ui.main_window.QTimer.singleShot") as schedule:
            MainWindow._start_network_broker(window)
        self.assertEqual(schedule.call_args.args[1], self.space.activate)
        restored.activate.assert_not_called()

    def test_wsl_does_not_advertise_an_unverified_robot_address(self):
        from ....services.network.broker import finder
        with mock.patch.object(finder, "is_wsl", return_value=True), mock.patch.object(finder, "port_open", return_value=True):
            self.assertEqual(finder.robot_endpoint(), ("", 0))
            self.store.set(keys.SYSTEM_BROKER_ROBOT_HOST, "192.168.1.10")
            self.store.set(keys.SYSTEM_BROKER_NETWORK_READY, True)
            self.assertEqual(finder.robot_endpoint(), ("192.168.1.10", 1883))
            self.store.set(keys.SYSTEM_BROKER_NETWORK_READY, False)
            self.assertEqual(finder.robot_endpoint(), ("", 0))


if __name__ == "__main__":
    unittest.main()
