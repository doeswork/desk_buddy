"""Qt + real Linux PTY coverage. Run: python -m studio.ui.components.wsl_serial_tests."""
from __future__ import annotations

import json
import os
import pty
import tempfile
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QProcess, QSettings
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from ...services.serial.wsl_usb import UsbCoordinator
from ...services.serial.wsl_usb_tests import BOARD, PORT, Host, finish
from ...storage.keys import WSL_USB_SELECTION
from ...storage.settings import Settings
from .firmware_flash import FirmwareFlash
from .serial_monitor import SerialMonitor
from .wsl_serial import WslSerialIntegration

APP = QApplication.instance() or QApplication([])


def events_until(predicate, seconds=2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for Qt")


class WslSerialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.preferences = Settings(QSettings(self.temp.name + "/settings.ini", QSettings.IniFormat))
        self.parent = QWidget()
        self.monitor = SerialMonitor()
        self.firmware = FirmwareFlash(self.monitor.disconnect_for_flash)
        self.host = Host()
        self.coordinator = UsbCoordinator(self.host)
        self.ui = WslSerialIntegration(self.monitor, self.firmware, self.parent,
                                       coordinator=self.coordinator, preferences=self.preferences)
        self.ui.timer.stop()
        self.fds = []

    def tearDown(self):
        self.ui.begin_shutdown()
        self.firmware.shutdown()
        self.monitor.shutdown()
        self.ui.shutdown()
        for fd in self.fds:
            os.close(fd)
        if self.ui.dialog:
            self.ui.dialog.close()
        self.monitor.close()
        self.firmware.close()
        self.parent.close()
        self.temp.cleanup()

    def terminal(self):
        master, slave = pty.openpty()
        self.fds.extend([master, slave])
        os.set_blocking(master, False)
        return master, os.ttyname(slave)

    def attach(self, address=None):
        if address:
            self.host.next_ports = (replace(PORT, address=address),)
        self.ui.action("attach", BOARD)
        finish(self.coordinator)
        self.ui._render()
        self.ui.poll()

    def test_construction_does_not_touch_windows(self):
        self.ui.poll()
        self.assertEqual(self.host.inspections, 0)
        self.assertEqual([b.text() for b in self.ui.buttons], ["Windows USB…", "Windows USB…"])

    def test_open_is_read_only_and_requires_selection(self):
        self.ui.open()
        finish(self.coordinator)
        self.ui._render()
        self.assertFalse(self.host.actions)
        self.assertFalse(self.ui.dialog.attach_button.isEnabled())
        self.ui.dialog.devices.setCurrentIndex(1)
        self.assertTrue(self.ui.dialog.attach_button.isEnabled())
        self.assertIn("guid-one", self.ui.dialog.identity.text())

    def test_unrecognized_boards_can_be_expanded(self):
        self.host.devices = (replace(BOARD, vid="abcd", description="Unknown", com_port=""),)
        self.ui.open()
        finish(self.coordinator)
        self.ui._render()
        self.assertEqual(self.ui.dialog.devices.count(), 1)
        self.ui.dialog.all_devices.setChecked(True)
        self.assertEqual(self.ui.dialog.devices.count(), 2)

    def test_empty_scan_offers_windows_setup(self):
        self.monitor.empty_scan.emit()
        self.assertIn("Windows USB", self.ui.labels[0].text())

    def test_verified_path_populates_both_pickers_without_connecting(self):
        self.attach()
        self.assertEqual(self.monitor.selected_port, PORT.address)
        self.assertEqual(self.firmware.selected_port, PORT.address)
        self.assertTrue(self.firmware._has_selected_port())
        self.assertFalse(self.monitor.serial.isOpen())
        self.assertEqual(self.preferences.get(WSL_USB_SELECTION), BOARD.identity)

    def test_remembered_selection_needs_explicit_attach_after_restart(self):
        self.preferences.set(WSL_USB_SELECTION, BOARD.identity)
        self.ui.open()
        finish(self.coordinator)
        self.ui._render()
        self.assertEqual(self.ui.dialog.devices.currentData().identity, BOARD.identity)
        self.assertFalse(self.host.actions or self.coordinator.session_device)

    def test_ambiguous_ports_are_presented_for_selection(self):
        self.host.next_ports = (PORT, replace(PORT, address="/dev/ttyUSB1"))
        self.ui.open()
        finish(self.coordinator)
        self.ui.action("attach", BOARD)
        finish(self.coordinator)
        self.ui._render()
        self.assertEqual(self.ui.dialog.linux_ports.count(), 3)
        self.ui.dialog.attach()
        self.assertIn("Select the matching", self.ui.dialog.status.text())
        self.ui.dialog.linux_ports.setCurrentIndex(2)
        self.ui.dialog.attach()
        finish(self.coordinator)
        self.ui.poll()
        self.assertEqual(self.monitor.selected_port, "/dev/ttyUSB1")

    def test_real_pty_reads_heartbeat_and_sends_provisioning(self):
        master, address = self.terminal()
        self.attach(address)
        self.monitor.connect()
        self.assertTrue(self.monitor.serial.isOpen())
        heartbeat = {"serial_heartbeat": True, "robot_id": "pty-board", "uptime_ms": 3000}
        os.write(master, (json.dumps(heartbeat) + "\n").encode())
        events_until(lambda: self.monitor._last_heartbeat is not None)
        self.assertEqual(self.monitor._last_heartbeat["robot_id"], "pty-board")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.assertEqual(self.monitor._send_wifi("Local robot", "password123"), "")
        APP.processEvents()
        data = os.read(master, 4096)
        command = json.loads(data)
        self.assertEqual(command["desk_buddy_command"], "set_wifi")
        self.assertEqual(command["ssid"], "Local robot")
        os.write(master, b'{"serial_provisioning":"wifi","status":"saved"}\n')
        events_until(lambda: "saved" in self.monitor.status.text().lower())

    def test_flash_reserves_monitor_and_never_repeats_failed_upload(self):
        _, address = self.terminal()
        self.attach(address)
        self.monitor.connect()
        with patch("studio.ui.components.firmware_flash.shutil.which", return_value="/bin/false"), \
                patch.object(self.firmware._process, "start") as start:
            self.firmware._start("flash", ["compile", "--upload"])
            self.assertTrue(self.coordinator.flash_active)
            self.assertFalse(self.monitor.serial.isOpen())
            self.monitor.connect()
            self.assertFalse(self.monitor.serial.isOpen())
            self.assertFalse(self.monitor.connect_button.isEnabled())
            self.assertFalse(self.coordinator.start("release", BOARD))
            self.firmware._finished(1, QProcess.NormalExit)
            self.assertFalse(self.coordinator.flash_active)
            self.assertTrue(self.monitor.serial.isOpen())
            self.ui.poll()
            start.assert_called_once()

    def test_qprocess_start_failure_releases_monitor_reservation(self):
        _, address = self.terminal()
        self.attach(address)
        self.monitor.connect()
        with patch("studio.ui.components.firmware_flash.shutil.which", return_value="/does/not/exist"):
            self.firmware._start("flash", [])
        events_until(lambda: not self.coordinator.flash_active)
        self.assertTrue(self.monitor.serial.isOpen())

    def test_missing_cli_does_not_disconnect_monitor(self):
        _, address = self.terminal()
        self.attach(address)
        self.monitor.connect()
        with patch("studio.ui.components.firmware_flash.shutil.which", return_value=None):
            self.firmware._start("flash", [])
        self.assertTrue(self.monitor.serial.isOpen())
        self.assertFalse(self.coordinator.flash_active)

    def test_usb_setup_blocks_flash_start(self):
        self.coordinator.running = True
        self.ui._render()
        with patch.object(self.firmware._process, "start") as start:
            self.firmware._start("flash", [])
        start.assert_not_called()
        self.coordinator.running = False

    def test_manual_disconnect_clears_reconnect_intent(self):
        _, address = self.terminal()
        self.attach(address)
        self.monitor.connect()
        self.assertTrue(self.ui._resume_monitor)
        self.monitor.disconnect()
        self.ui.poll()
        self.assertFalse(self.ui._resume_monitor or self.monitor.serial.isOpen())

    def test_recovery_moves_monitor_to_replacement_path(self):
        _, address = self.terminal()
        self.attach(address)
        self.monitor.connect()
        _, replacement = self.terminal()
        self.host.ports = ()
        self.host.devices = (replace(BOARD, busid="5-2"),)
        self.host.next_ports = (replace(PORT, address=replacement),)
        self.coordinator._next_retry = 0
        # Slow host operation lets Qt observe disappearance first.
        original = self.host.inspect
        import threading
        gate = threading.Event()
        def inspect():
            gate.wait(1)
            return original()
        with patch.object(self.host, "inspect", side_effect=inspect):
            self.ui.poll()
            self.assertFalse(self.monitor.serial.isOpen())
            gate.set()
            finish(self.coordinator)
        self.ui.poll()
        self.assertTrue(self.monitor.serial.isOpen())
        self.assertEqual(self.monitor.selected_port, replacement)
        self.assertEqual(self.firmware.selected_port, replacement)

    def test_read_only_refresh_does_not_disconnect_monitor(self):
        _, address = self.terminal()
        self.attach(address)
        self.monitor.connect()
        self.ui.open()
        finish(self.coordinator)
        self.ui.poll()
        self.assertTrue(self.monitor.serial.isOpen())

    def test_release_closes_monitor_and_disables_reconnect(self):
        _, address = self.terminal()
        self.attach(address)
        self.monitor.connect()
        self.ui.release()
        finish(self.coordinator)
        self.ui.poll()
        self.assertFalse(self.monitor.serial.isOpen() or self.ui._resume_monitor)
        self.assertEqual(self.host.actions[-1][0], "detach")
        self.assertEqual(self.monitor.port.findData(address), -1)

    def test_native_debug_tray_does_not_create_usb_coordinator(self):
        from types import SimpleNamespace
        from .debug_tray import DebugTray
        records = SimpleNamespace(latest_id=lambda: 0, count=lambda: 0, recent=lambda *a, **k: [])
        with patch("studio.services.wsl.windows.is_wsl", return_value=False), \
                patch("studio.ui.components.wsl_serial.UsbCoordinator") as coordinator:
            tray = DebugTray(records, records, SimpleNamespace(status="Ready"), lambda: None, broker_host=lambda: "")
        self.assertIsNone(tray.wsl_serial)
        coordinator.assert_not_called()
        tray.shutdown()
        tray.close()


if __name__ == "__main__":
    unittest.main()
