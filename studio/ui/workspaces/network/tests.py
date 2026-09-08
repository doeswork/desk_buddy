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
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from ....storage import keys
from ....storage.settings import Settings
from ....services.network.broker import system
from ....services.network.broker.setup import (
    AccountReloadCoordinator,
    AccountReloadStatus,
    Coordinator,
    SetupCancelled,
    SetupStatus,
)
from ....services.network.wsl import AccessStatus
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
        self.space.environment = SimpleNamespace(wsl=False)
        self.addCleanup(self.cleanup_timer)
        self.space.refresh = mock.Mock()

    def cleanup_timer(self):
        if self.space._setup_timer is not None:
            self.space._setup_timer.stop()
        if self.space._account_reload_timer is not None:
            self.space._account_reload_timer.stop()
        if self.space._robot_access_timer is not None:
            self.space._robot_access_timer.stop()

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

    def test_wsl_offers_opt_in_robot_access_without_downgrading_broker(self):
        self.space.environment = SimpleNamespace(wsl=True)
        state = SetupStatus(
            "ready", "Connected as “studio” at 127.0.0.1:1883.", connected=True,
        )
        self.space.robot_access.status = AccessStatus(
            "disabled", "Robots cannot reach this WSL broker yet."
        )
        widget = self.widget(state)
        labels = [label.text() for label in widget.findChildren(QLabel)]
        buttons = [button.text() for button in widget.findChildren(QPushButton)]
        self.assertIn("Studio is connected", labels)
        self.assertIn("Connect robots on your network", labels)
        self.assertIn("Enable robot access", buttons)
        self.assertNotIn("Retry setup", buttons)

    def test_wsl_ready_card_shows_windows_address_and_disable(self):
        self.space.environment = SimpleNamespace(wsl=True)
        self.space.robot_access.status = AccessStatus(
            "ready", "Robot address: 192.168.1.10:1883",
            host="192.168.1.10", ready=True,
            detail="A physical robot connection has not yet been confirmed.",
        )
        widget = self.widget(SetupStatus("ready", "Connected", connected=True))
        labels = [label.text() for label in widget.findChildren(QLabel)]
        buttons = [button.text() for button in widget.findChildren(QPushButton)]
        self.assertIn("Robot address: 192.168.1.10:1883", labels)
        self.assertTrue(any("not yet been confirmed" in text for text in labels))
        self.assertIn("Disable robot access", buttons)

    def test_wsl_repair_and_failure_never_show_a_stale_address(self):
        self.space.environment = SimpleNamespace(wsl=True)
        for access, button in (
            (AccessStatus("repair", "Needs repair", detail="Address changed"), "Repair robot access"),
            (AccessStatus("failed", "Needs attention", detail="Port did not answer"), "Retry robot access"),
        ):
            self.space.robot_access.status = access
            widget = self.widget(SetupStatus("ready", "Connected", connected=True))
            labels = [label.text() for label in widget.findChildren(QLabel)]
            buttons = [item.text() for item in widget.findChildren(QPushButton)]
            self.assertFalse(any("192.168." in text for text in labels))
            self.assertIn(button, buttons)

    def test_wsl_progress_states_do_not_offer_duplicate_actions(self):
        self.space.environment = SimpleNamespace(wsl=True)
        for state in ("checking", "enabling", "disabling"):
            self.space.robot_access.status = AccessStatus(state, "Working")
            self.space.robot_access.running = True
            widget = self.widget(SetupStatus("ready", "Connected", connected=True))
            buttons = [button.text() for button in widget.findChildren(QPushButton)]
            self.assertFalse(any("robot access" in text.lower() for text in buttons))
        self.space.robot_access.running = False

    def test_wsl_cancelled_action_preserves_the_observed_access_state(self):
        self.space.environment = SimpleNamespace(wsl=True)
        broker = SetupStatus("ready", "Connected", connected=True)
        for access, expected in (
            (AccessStatus("cancelled", "Cancelled", detail="Not changed"), "Enable robot access"),
            (AccessStatus("cancelled", "Cancelled", host="192.168.1.10", ready=True,
                          detail="Still enabled"), "Disable robot access"),
        ):
            self.space.robot_access.status = access
            widget = self.widget(broker)
            buttons = [button.text() for button in widget.findChildren(QPushButton)]
            self.assertIn(expected, buttons)

    def test_failed_disable_retries_disable_not_enable(self):
        self.space.environment = SimpleNamespace(wsl=True)
        self.space.robot_access.status = AccessStatus(
            "failed", "Removal failed", detail="Windows error", action="disable"
        )
        with mock.patch.object(self.space, "start_robot_access") as start:
            widget = self.widget(SetupStatus("ready", "Connected", connected=True))
            retry = next(button for button in widget.findChildren(QPushButton)
                         if button.text() == "Retry disabling robot access")
            retry.click()
        start.assert_called_once_with("disable")

    def test_wsl_card_buttons_dispatch_only_the_requested_host_action(self):
        self.space.environment = SimpleNamespace(wsl=True)
        broker = SetupStatus("ready", "Connected", connected=True)
        with mock.patch.object(self.space, "start_robot_access") as start:
            self.space.robot_access.status = AccessStatus("disabled", "Disabled")
            widget = self.widget(broker)
            next(button for button in widget.findChildren(QPushButton)
                 if button.text() == "Enable robot access").click()
            start.assert_called_once_with("enable")
            start.reset_mock()
            self.space.robot_access.status = AccessStatus(
                "ready", "Ready", host="192.168.1.10", ready=True
            )
            widget = self.widget(broker)
            next(button for button in widget.findChildren(QPushButton)
                 if button.text() == "Disable robot access").click()
            start.assert_called_once_with("disable")

    def test_native_platforms_never_show_wsl_access_card(self):
        self.space.environment = SimpleNamespace(wsl=False)
        self.space.robot_access.status = AccessStatus("disabled", "Not configured")
        widget = self.widget(SetupStatus("ready", "Connected", connected=True))
        buttons = [button.text() for button in widget.findChildren(QPushButton)]
        self.assertNotIn("Enable robot access", buttons)
        self.assertNotIn("Disable robot access", buttons)

    def test_custom_remote_broker_inside_wsl_does_not_show_forwarding(self):
        self.space.environment = SimpleNamespace(wsl=True)
        self.store.set(keys.SYSTEM_BROKER_HOST, "broker.example")
        self.space.robot_access.status = AccessStatus("disabled", "Not configured")
        widget = self.widget(SetupStatus(
            "ready", "Connected", connected=True,
            robot_host="broker.example", network_ready=True,
        ))
        buttons = [button.text() for button in widget.findChildren(QPushButton)]
        self.assertNotIn("Enable robot access", buttons)

    def test_wsl_inspection_suppresses_saved_endpoint_until_verified(self):
        self.space.environment = SimpleNamespace(wsl=True)
        self.space.setup.status = SetupStatus("ready", "Connected", connected=True)
        self.store.set(keys.SYSTEM_BROKER_ROBOT_HOST, "192.168.1.10")
        self.store.set(keys.SYSTEM_BROKER_NETWORK_READY, True)
        with mock.patch.object(self.space.robot_access, "start", return_value=True):
            self.space.start_robot_access("inspect")
        self.assertEqual(self.store.get(keys.SYSTEM_BROKER_ROBOT_HOST), "")
        self.assertFalse(self.store.get(keys.SYSTEM_BROKER_NETWORK_READY))

    def test_windows_failure_does_not_change_connected_broker_state(self):
        self.space.environment = SimpleNamespace(wsl=True)
        broker = SetupStatus("ready", "Connected", connected=True)
        self.space.setup.status = broker
        self.space.robot_access.status = AccessStatus(
            "failed", "Windows access needs attention", detail="UAC failed"
        )
        widget = self.widget()
        labels = [label.text() for label in widget.findChildren(QLabel)]
        self.assertEqual(self.space.setup.status, broker)
        self.assertIn("Studio is connected", labels)
        self.assertIn("Robot access needs attention", labels)

    def test_users_never_advertise_the_private_wsl_address(self):
        self.space.environment = SimpleNamespace(wsl=True)
        self.space.setup.status = SetupStatus("ready", "Connected", connected=True)
        self.space.broker_host = mock.Mock(return_value="")
        self.space.accounts = mock.Mock(return_value=[])
        self.space.running = mock.Mock(return_value=True)
        self.space.system_broker = mock.Mock(return_value=SimpleNamespace(port=1883))
        page = self.space.find("accounts")
        page._widget = None
        widget = page.widget()
        labels = [label.text() for label in widget.findChildren(QLabel)]
        self.assertIn("Robot access is not ready", labels)
        self.assertFalse(any("172." in text for text in labels))

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

    def accounts_widget(self, entries):
        self.space.accounts = mock.Mock(return_value=entries)
        self.space.running = mock.Mock(return_value=True)
        self.space.broker_host = mock.Mock(return_value="192.168.1.10")
        self.space.system_broker = mock.Mock(
            return_value=SimpleNamespace(port=1883)
        )
        page = self.space.find("accounts")
        page._widget = None
        return page, page.widget()

    def test_saved_password_is_shown_while_reload_is_pending(self):
        self.store.set(keys.SYSTEM_BROKER_RELOAD_PENDING, True)
        self.space.account_reload.status = AccountReloadStatus(
            "applying", "Applying broker user changes…"
        )
        self.space.account_reload.running = True
        account = system.Account(
            "ubuntu-test", ("ubuntu-test/#",), "saved-password"
        )
        page, widget = self.accounts_widget([account])
        page._credentials = (account.name, account.password)
        page._widget = None
        widget = page.widget()
        labels = [label.text() for label in widget.findChildren(QLabel)]
        values = [field.text() for field in widget.findChildren(QLineEdit)]
        self.assertIn("User ubuntu-test is saved", labels)
        self.assertIn("Applying broker user changes", labels)
        self.assertIn("saved-password", values)
        self.space.account_reload.running = False

    def test_studio_recorded_password_can_be_shown_again(self):
        account = system.Account(
            "ubuntu-test", ("ubuntu-test/#",), "saved-password"
        )
        page, widget = self.accounts_widget([account])
        show = next(
            label for label in widget.findChildren(QLabel)
            if label.text() == "Show"
        )
        show.mousePressEvent(None)
        self.assertEqual(page._credentials, ("ubuntu-test", "saved-password"))

    def test_hand_created_account_has_no_show_action(self):
        _page, widget = self.accounts_widget([
            system.Account("made-by-hand", ("made-by-hand/#",), "")
        ])
        self.assertFalse(any(
            label.text() == "Show" for label in widget.findChildren(QLabel)
        ))

    def test_account_actions_are_disabled_while_reload_runs(self):
        self.store.set(keys.SYSTEM_BROKER_RELOAD_PENDING, True)
        self.space.account_reload.running = True
        account = system.Account(
            "ubuntu-test", ("ubuntu-test/#",), "saved-password"
        )
        page, widget = self.accounts_widget([account])
        actions = [label.text() for label in widget.findChildren(QLabel)]
        self.assertIn("Show", actions)
        self.assertNotIn("Edit", actions)
        self.assertNotIn("Remove", actions)
        add = page.build_header_actions()
        self.assertIsNotNone(add)
        self.assertFalse(add.isEnabled())
        self.space.account_reload.running = False

    def test_pending_reload_has_retry_on_users_and_broker_pages(self):
        self.store.set(keys.SYSTEM_BROKER_RELOAD_PENDING, True)
        self.space.account_reload.status = AccountReloadStatus(
            "failed",
            "Broker user changes still need to be applied.",
            "Authorization was cancelled.",
        )
        with mock.patch.object(self.space, "start_account_reload") as start:
            _page, users = self.accounts_widget([])
            retry = next(
                button for button in users.findChildren(QPushButton)
                if button.text() == "Retry applying changes"
            )
            retry.click()
            start.assert_called_once_with(retry=True)
            start.reset_mock()

            broker = self.widget(SetupStatus("ready", "Connected", True))
            retry = next(
                button for button in broker.findChildren(QPushButton)
                if button.text() == "Retry applying changes"
            )
            retry.click()
            start.assert_called_once_with(retry=True)

    def test_account_reload_failure_does_not_downgrade_the_broker(self):
        broker = SetupStatus("ready", "Connected", connected=True)
        self.space.setup.status = broker
        self.store.set(keys.SYSTEM_BROKER_USER, "studio")
        self.store.set(keys.SYSTEM_BROKER_PASSWORD, "studio-secret")
        self.store.set(keys.SYSTEM_BROKER_VERIFIED, True)
        self.store.set(keys.SYSTEM_BROKER_SETUP_PAUSED, False)
        self.store.set(keys.SYSTEM_BROKER_RELOAD_PENDING, True)

        def cancelled(_request, _emit):
            raise SetupCancelled("Authorization was cancelled.")

        self.space.account_reload = AccountReloadCoordinator(cancelled)
        self.space.start_account_reload(retry=True)
        deadline = time.monotonic() + 2
        while self.space.account_reload.running and time.monotonic() < deadline:
            self.space._poll_account_reload()
            time.sleep(0.005)

        self.assertEqual(self.space.setup.status, broker)
        self.assertTrue(self.store.get(keys.SYSTEM_BROKER_VERIFIED))
        self.assertFalse(self.store.get(keys.SYSTEM_BROKER_SETUP_PAUSED))
        self.assertTrue(self.store.get(keys.SYSTEM_BROKER_RELOAD_PENDING))
        self.assertEqual(self.space.account_reload.status.state, "cancelled")

    def test_account_reload_success_clears_only_the_pending_flag(self):
        broker = SetupStatus("ready", "Connected", connected=True)
        self.space.setup.status = broker
        self.store.set(keys.SYSTEM_BROKER_USER, "studio")
        self.store.set(keys.SYSTEM_BROKER_PASSWORD, "studio-secret")
        self.store.set(keys.SYSTEM_BROKER_VERIFIED, True)
        self.store.set(keys.SYSTEM_BROKER_RELOAD_PENDING, True)
        self.space.account_reload = AccountReloadCoordinator(
            lambda request, emit: AccountReloadStatus(
                "ready", "Broker user changes are active."
            )
        )
        self.space.start_account_reload(retry=True)
        deadline = time.monotonic() + 2
        while self.space.account_reload.running and time.monotonic() < deadline:
            self.space._poll_account_reload()
            time.sleep(0.005)

        self.assertEqual(self.space.setup.status, broker)
        self.assertTrue(self.store.get(keys.SYSTEM_BROKER_VERIFIED))
        self.assertFalse(self.store.get(keys.SYSTEM_BROKER_RELOAD_PENDING))

    def test_network_activation_routes_an_existing_pending_reload(self):
        self.store.set(keys.SYSTEM_BROKER_RELOAD_PENDING, True)
        self.space.setup = Coordinator(
            lambda request, emit: SetupStatus(
                "ready", "Connected", connected=True, user=request["user"]
            )
        )
        with mock.patch.object(self.space, "start_account_reload") as start:
            self.space.activate()
            deadline = time.monotonic() + 2
            while self.space.setup.running and time.monotonic() < deadline:
                self.space._poll_setup()
                time.sleep(0.005)
        start.assert_called_once_with()
        self.assertTrue(
            self.store.get(keys.SYSTEM_BROKER_RELOAD_PENDING),
            "only the dedicated reload completion may clear this flag",
        )

    def test_create_page_treats_written_pending_account_as_created(self):
        add = self.space.find("add_account")
        users = self.space.find("accounts")
        add._name = "new-robot"
        with (
            mock.patch.object(system, "accounts", return_value=[]),
            mock.patch.object(
                self.space,
                "create_account",
                return_value=system.AccountChange(changed=True),
            ),
            mock.patch.object(users, "created") as created,
            mock.patch.object(self.space, "go_to") as go_to,
        ):
            add.create()
        self.assertEqual(created.call_args.args[0], "new-robot")
        self.assertEqual(len(created.call_args.args[1]), 24)
        go_to.assert_called_once_with("accounts")

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
