"""Firmware setup flow without network, real tool installs, or physical uploads."""
import io
import os
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from ...models.config.robot_profiles import RobotProfile
from ...services.network.broker.system import AccountChange
from ...services.firmware import install_firmware_flasher as tools
from .firmware_flash import FirmwareFlash
from .robot_profile_dialog import RobotProfileDialog

APP = QApplication.instance() or QApplication([])
READY = b'{"platforms":[{"id":"esp32:esp32","installed_version":"3.3.0"}]}'


class BootstrapTests(unittest.TestCase):
    def test_platform_archives(self):
        self.assertIn("Linux_64bit.tar.gz", tools.archive_name("Linux", "x86_64"))
        self.assertIn("Linux_ARM64", tools.archive_name("Linux", "aarch64"))
        self.assertIn("macOS_ARM64", tools.archive_name("Darwin", "arm64"))
        self.assertIn("Windows_64bit.zip", tools.archive_name("Windows", "AMD64"))
        with self.assertRaises(RuntimeError):
            tools.archive_name("Unknown", "unknown")

    def test_core_metadata(self):
        self.assertTrue(tools.has_esp32(READY))
        self.assertTrue(tools.has_esp32(b'[{"id":"esp32:esp32","installed":"3.0.0"}]'))
        self.assertFalse(tools.has_esp32(b'{"platforms":[]}'))
        self.assertFalse(tools.has_esp32(b'{"platforms":[{"id":"esp32:esp32"}]}'))

    def test_install_extracts_only_executable(self):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as package:
            for name, contents in (("arduino-cli", b"test executable"), ("../../unwanted", b"bad")):
                info = tarfile.TarInfo(name)
                info.size = len(contents)
                package.addfile(info, io.BytesIO(contents))
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(tools, "archive_name", return_value="arduino-cli_test.tar.gz"), \
                patch.object(tools.urllib.request, "urlopen", return_value=io.BytesIO(archive.getvalue())):
            target = Path(folder) / "bin" / "arduino-cli"
            self.assertEqual(tools.install_cli(target, threading.Event()), str(target))
            self.assertEqual(target.read_bytes(), b"test executable")
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_cancelled_download_does_not_publish_binary(self):
        cancelled = threading.Event()
        cancelled.set()
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(tools.urllib.request, "urlopen", return_value=io.BytesIO(b"bad")):
            target = Path(folder) / "arduino-cli"
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                tools.install_cli(target, cancelled)
            self.assertFalse(target.exists())

    def test_path_or_managed_cli_is_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "arduino-cli"
            target.touch()
            with patch.object(tools, "managed_cli", return_value=target), patch.object(tools.shutil, "which", return_value=None):
                self.assertEqual(tools.find_cli(), str(target))
            with patch.object(tools.shutil, "which", return_value="/usr/bin/arduino-cli"):
                self.assertEqual(tools.find_cli(), "/usr/bin/arduino-cli")


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.profile = RobotProfile(
            profile_id="test-profile",
            name="Test robot",
            wifi_ssid="Test Wi-Fi",
            wifi_password="wifi-password",
            broker_server="broker.example",
            mqtt_user="test-robot",
            mqtt_password="mqtt-password",
            client_id="test-robot",
            broker_kind="custom",
        )
        self.profile_store = Mock()
        self.profile_store.all.return_value = []
        self.profile_store.find.return_value = None
        patch("studio.ui.components.firmware_flash.profiles", return_value=self.profile_store).start()
        patch("studio.ui.components.firmware_flash.settings", return_value=Mock()).start()
        patch("studio.ui.components.firmware_flash.users", return_value=Mock()).start()
        patch.object(
            RobotProfileDialog,
            "exec",
            return_value=QDialog.DialogCode.Accepted,
        ).start()
        patch.object(RobotProfileDialog, "profile", return_value=self.profile).start()
        self.view = FirmwareFlash()
        self.setup = self.view._installer
        self.start = patch.object(self.setup._process, "start").start()
        patch.object(self.setup._process, "readAllStandardOutput", return_value=b"").start()
        self.locator = patch.object(tools, "find_cli", return_value="/fake/arduino-cli").start()
        self.question = patch.object(QMessageBox, "question", return_value=QMessageBox.Yes).start()
        self.callback = Mock()

    def tearDown(self):
        self.view.shutdown()
        self.view.close()
        patch.stopall()

    def finish(self, raw=b"", code=0):
        self.setup._output = bytearray(raw)
        self.setup._finished(code, QProcess.NormalExit)

    def test_startup_does_not_install_or_prompt(self):
        self.question.assert_not_called()
        self.start.assert_not_called()

    def test_ready_tools_continue_without_prompt(self):
        self.setup.ensure(self.callback)
        self.finish(READY)
        self.callback.assert_called_once()
        self.question.assert_not_called()
        self.assertEqual(self.view._task, "")

    def test_missing_core_installs_and_verifies_before_continuing(self):
        self.setup.ensure(self.callback)
        self.finish(b'{"platforms":[]}')
        self.question.assert_called_once()
        self.assertEqual(self.setup._phase, "index")
        self.finish()
        self.assertEqual(self.start.call_args.args[1][:3], ["core", "install", "esp32:esp32"])
        self.finish()
        self.callback.assert_not_called()
        self.finish(READY)
        self.callback.assert_called_once()

    def test_missing_cli_offers_download_then_board_install(self):
        self.locator.return_value = ""
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(tools, "managed_cli", return_value=Path(folder) / "arduino-cli"), \
                patch.object(tools, "install_cli", return_value="/managed/arduino-cli") as download:
            self.setup.ensure(self.callback)
            from PySide6.QtTest import QTest
            for _ in range(100):
                QTest.qWait(10)
                if self.setup._phase == "index":
                    break
            download.assert_called_once()
        self.assertEqual(self.setup.program, "/managed/arduino-cli")
        self.assertEqual(self.setup._phase, "index")
        self.callback.assert_not_called()

    def test_declining_setup_keeps_monitor_and_upload_untouched(self):
        self.locator.return_value = ""
        self.question.return_value = QMessageBox.No
        with patch.object(self.view, "_before_flash") as before:
            self.setup.ensure(self.callback)
        self.callback.assert_not_called()
        before.assert_not_called()
        self.assertFalse(self.setup.busy)

    def test_stop_cancels_continuation(self):
        self.setup.ensure(self.callback)
        self.view.stop()
        self.finish(READY)
        self.callback.assert_not_called()
        self.assertFalse(self.setup.busy)

    def test_failure_does_not_continue(self):
        self.setup.ensure(self.callback)
        self.finish(b'{"platforms":[]}')
        self.finish(b"Download failed", code=1)
        self.callback.assert_not_called()
        self.assertIn("failed", self.view.status.text())
        self.assertFalse(self.setup.busy)

    def test_missing_indexes_can_be_repaired(self):
        self.setup.ensure(self.callback)
        self.finish(b"package index missing", code=1)
        self.assertEqual(self.setup._phase, "index")
        self.question.assert_called_once()

    def test_failed_verification_does_not_prompt_forever(self):
        self.setup.ensure(self.callback)
        self.finish(b'{"platforms":[]}')
        self.finish()
        self.finish()
        self.finish(b'{"platforms":[]}')
        self.assertFalse(self.setup.busy)
        self.question.assert_called_once()
        self.callback.assert_not_called()

    def test_scan_resumes_after_check(self):
        with patch.object(self.view, "_start") as run:
            self.view.scan()
            self.finish(READY)
        self.assertEqual(run.call_args.args, ("scan", ["board", "list", "--format", "json"]))

    def test_build_resumes_after_check(self):
        with patch.object(self.view, "_start") as run:
            self.view.compile()
            self.finish(READY)
        self.assertEqual(run.call_args.args[0], "build")

    def test_flash_asks_for_upload_only_after_setup(self):
        self.view.port.addItem("/dev/ttyUSB0", "/dev/ttyUSB0")
        with patch.object(self.view, "_start") as run:
            self.view.flash()
            self.question.assert_not_called()
            self.finish(READY)
        self.assertEqual(run.call_args.args[0], "flash")
        self.assertEqual(self.question.call_args.args[1], "Flash firmware?")

    def test_account_reload_wait_is_async_and_then_resumes_flash(self):
        network = Mock()
        network.create_account.return_value = AccountChange(changed=True)
        network.account_reload_pending.return_value = True
        network.account_reload.running = True
        network.account_problem = ""
        view = FirmwareFlash(network=network)
        profile = RobotProfile(
            profile_id="local-profile",
            name="Local robot",
            wifi_ssid="Test Wi-Fi",
            wifi_password="wifi-password",
            broker_server="192.168.0.248",
            mqtt_user="local-robot",
            mqtt_password="mqtt-password",
            client_id="local-robot",
        )
        view._pending_profile = profile
        view._pending_flash_arguments = ["compile", "--upload"]
        try:
            with patch.object(QApplication, "processEvents") as nested_events, \
                    patch.object(view, "_start") as start:
                self.assertFalse(view._prepare_local_account(profile))
                nested_events.assert_not_called()
                start.assert_not_called()
                self.assertTrue(view._account_reload_timer.isActive())

                network.account_reload_pending.return_value = False
                view._poll_account_reload()

                start.assert_called_once_with("flash", ["compile", "--upload"])
                self.assertFalse(view._account_reload_timer.isActive())
        finally:
            view.shutdown()
            view.close()

    def test_profile_waits_for_first_serial_heartbeat_after_reconnect(self):
        monitor = Mock()
        monitor.send_profile.return_value = ""
        self.view._serial_monitor = monitor
        self.view._pending_profile = self.profile
        self.view._provision_state = "reconnect"

        self.view._serial_opened("/dev/ttyUSB0")
        self.assertEqual(self.view._provision_state, "serial-ready")
        monitor.send_profile.assert_not_called()

        self.view._serial_heartbeat({"serial_heartbeat": True, "uptime_ms": 1000})
        monitor.send_profile.assert_called_once_with(self.profile)
        self.assertEqual(self.view._provision_state, "ack")

    def test_saved_profile_can_be_retried_without_flashing(self):
        monitor = Mock()
        monitor.serial.isOpen.return_value = True
        self.view._serial_monitor = monitor
        self.view.port.addItem("/dev/ttyUSB0", "/dev/ttyUSB0")
        self.profile_store.find.return_value = self.profile

        with patch.object(self.view, "_start") as flash:
            self.view.retry_provisioning()

        flash.assert_not_called()
        self.assertIs(self.view._pending_profile, self.profile)
        self.assertEqual(self.view._provision_state, "serial-ready")
        monitor.send_profile.assert_not_called()

    def test_transport_rejection_fails_profile_without_waiting_for_timeout(self):
        self.view._pending_profile = self.profile
        self.view._provision_state = "ack"
        with patch.object(self.view, "_fail_provisioning") as failed:
            self.view._provision_response({
                "serial_provisioning": "error",
                "status": "error",
                "error": "Command must be valid JSON",
            })
        failed.assert_called_once_with("Command must be valid JSON")

    def test_duplicate_actions_do_not_overlap_setup(self):
        self.setup.ensure(self.callback)
        self.setup.ensure(Mock())
        self.start.assert_called_once()

    def test_timeout_clears_request(self):
        self.setup.ensure(self.callback)
        self.setup._timeout.timeout.emit()
        self.assertFalse(self.setup.busy)
        self.assertIn("timed out", self.view.status.text())
        self.callback.assert_not_called()

    def test_download_failure_releases_setup_for_retry(self):
        self.setup.busy = True
        self.setup._phase = "download"
        self.setup._callback = self.callback
        self.setup._events.put(("failed", "Connection lost"))
        self.setup._poll()
        self.assertFalse(self.setup.busy)
        self.assertIn("Connection lost", self.view.status.text())
        self.callback.assert_not_called()

    def test_repeated_cancel_waits_for_download_to_finish(self):
        self.setup.busy = True
        self.setup._phase = "download"
        self.setup._callback = self.callback
        self.setup.stop()
        self.setup.stop()
        self.assertTrue(self.setup.busy)
        self.setup._events.put(("downloaded", "/fake/arduino-cli"))
        self.setup._poll()
        self.assertFalse(self.setup.busy)
        self.start.assert_not_called()
        self.callback.assert_not_called()

    def test_process_cancellation_waits_before_allowing_another_setup(self):
        self.setup.ensure(self.callback)
        with patch.object(self.setup._process, "state", return_value=QProcess.Running):
            self.setup.stop()
        self.assertTrue(self.setup.busy)
        self.finish(code=9)
        self.assertFalse(self.setup.busy)
        self.question.assert_not_called()
        self.callback.assert_not_called()

    def test_shutdown_discards_pending_action(self):
        self.setup.ensure(self.callback)
        self.view.shutdown()
        self.finish(READY)
        self.callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
