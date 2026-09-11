"""Mocked Windows/WSL lifecycle tests: python -m studio.services.serial.wsl_usb_tests."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from . import wsl_permissions as permissions
from .wsl_usb import (
    LinuxPort, UsbAccessStatus, UsbCoordinator, UsbDevice, WindowsUsbBackend,
    matching_ports, parse_devices, same_device, windows,
)

BOARD = UsbDevice(r"USB\VID_10C4&PID_EA60\ABC", "CP210x", "2-3", "guid-one", "10c4", "ea60", "ABC", "COM8")
PORT = LinuxPort("/dev/ttyUSB0", "10c4", "ea60", "ABC", (10, 188))
STATUS = UsbAccessStatus(devices=(BOARD,), tool=r"C:\Program Files\usbipd-win\usbipd.exe",
                         version="5.3.0", winget="winget.exe", windows_temp=r"C:\Temp", service_running=True)


class Host:
    def __init__(self, device=BOARD):
        self.devices = (device,)
        self.ports = ()
        self.next_ports = (PORT,)
        self.actions = []
        self.granted = []
        self.inspections = 0
        self.supported = True

    def inspect(self):
        self.inspections += 1
        return replace(STATUS, devices=self.devices, tool=STATUS.tool if self.supported else "")

    def linux_ports(self):
        return self.ports

    def tool_action(self, status, device, action, *, elevated=False):
        self.actions.append((action, device.busid, elevated))
        if action == "bind":
            self.devices = (replace(device, persisted_guid="guid-one"),)
        if action == "attach":
            self.devices = (replace(device, attached=True),)
            self.ports = self.next_ports
        if action == "detach":
            self.devices = (replace(device, attached=False),)
            self.ports = ()

    def grant_access(self, port):
        self.granted.append(port)

    def install(self, status):
        self.supported = True


def finish(coordinator):
    coordinator._thread.join(2)
    if coordinator._thread.is_alive():
        coordinator.cancel()
        coordinator._thread.join(2)
        raise AssertionError("USB worker did not finish")
    coordinator.poll()
    return coordinator.status


class DiscoveryTests(unittest.TestCase):
    def test_json_identity_is_separate_from_routes(self):
        ports = [{"DeviceID": "COM8", "Ancestors": [r"USB\CHILD", BOARD.instance_id]}]
        raw = {"InstanceId": BOARD.instance_id, "Description": "CP210x", "BusId": "2-3",
               "PersistedGuid": "guid-one", "ClientIPAddress": "172.20.0.2"}
        device, = parse_devices({"Devices": [raw]}, ports)
        self.assertEqual((device.identity, device.com_port, device.busid, device.vid, device.serial),
                         ("guid-one", "COM8", "2-3", "10c4", "ABC"))
        self.assertTrue(device.attached and device.candidate)
        self.assertTrue(same_device(device, replace(device, busid="9-2", com_port="COM21")))

    def test_unplugged_shared_device_has_no_busid(self):
        device, = parse_devices({"Devices": [{"InstanceId": BOARD.instance_id, "PersistedGuid": "guid-one"}]}, [])
        self.assertTrue(device.shared)
        self.assertFalse(device.attached or device.busid)

    def test_location_token_is_not_a_serial_number(self):
        device, = parse_devices({"Devices": [{"InstanceId": r"USB\VID_1A86&PID_7523\5&123&0&2"}]}, [])
        self.assertEqual(device.serial, "")
        self.assertEqual(device.vid, "1a86")

    def test_unknown_devices_remain_available(self):
        device, = parse_devices({"Devices": [{"InstanceId": r"USB\VID_ABCD&PID_1234\SERIAL", "Description": "Unknown"}]}, [])
        self.assertFalse(device.candidate)
        self.assertEqual(device.pid, "1234")

    def test_invalid_state_is_actionable(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            parse_devices({"Devices": {}}, [])

    def test_version_requirement(self):
        for version in ("", "4.4.0", "unknown"):
            self.assertFalse(replace(STATUS, version=version).supported)
        for version in ("5.0.0", "usbipd-win 5.3.0", "6.0.0"):
            self.assertTrue(replace(STATUS, version=version).supported)

    def test_serial_number_rejects_same_model(self):
        self.assertEqual(matching_ports(BOARD, (PORT, replace(PORT, serial="OTHER"))), (PORT,))

    def test_missing_support_still_discovers_com_identity(self):
        data = {"serialPorts": [{"DeviceID": "COM8", "PNPDeviceID": BOARD.instance_id,
                                "Ancestors": [BOARD.instance_id], "Name": "Board"}], "winget": "winget.exe"}
        backend = WindowsUsbBackend("Ubuntu")
        with patch.object(backend, "check_environment"), patch.object(backend, "linux_ports", return_value=()), \
                patch.object(windows, "powershell", return_value=SimpleNamespace(returncode=0, stdout=json.dumps(data))):
            result = backend.inspect()
        self.assertEqual(result.state, "needs_install")
        self.assertEqual((result.devices[0].com_port, result.devices[0].vid), ("COM8", "10c4"))


class HostOperationTests(unittest.TestCase):
    def test_powershell_restores_exe_resolution_only_in_its_process(self):
        import base64
        with patch.object(windows, "run") as run:
            windows.powershell("Write-Output ready", locator=lambda _: "powershell.exe")
        command = run.call_args.args[0]
        script = base64.b64decode(command[-1]).decode("utf-16le")
        self.assertIn("GetEnvironmentVariable('PATHEXT','Machine')", script)
        self.assertIn("-notcontains '.EXE'", script)
        self.assertNotIn("SetEnvironmentVariable", script)
        self.assertTrue(script.endswith("Write-Output ready"))

    def test_missing_interop(self):
        with self.assertRaisesRegex(RuntimeError, "interoperability"):
            windows.powershell("", locator=lambda _: "")

    def test_unsupported_wsl(self):
        with patch.object(windows, "is_wsl", return_value=False), self.assertRaisesRegex(RuntimeError, "WSL2"):
            WindowsUsbBackend("Ubuntu").check_environment()

    def test_old_kernel(self):
        with patch.object(windows, "is_wsl", return_value=True), \
                patch("platform.release", return_value="4.19.1-microsoft-WSL2"), self.assertRaisesRegex(RuntimeError, "kernel"):
            WindowsUsbBackend("Ubuntu").check_environment()

    def test_missing_winget(self):
        with self.assertRaisesRegex(RuntimeError, "winget"):
            WindowsUsbBackend().install(replace(STATUS, winget=""))

    def test_installer_is_interactive_and_does_not_restart(self):
        result = SimpleNamespace(returncode=0, stdout='installer progress\n{"exitCode":0}')
        with patch.object(windows, "powershell", return_value=result) as ps:
            WindowsUsbBackend().install(STATUS)
        script = ps.call_args.args[0]
        self.assertIn("install --interactive --exact dorssel.usbipd-win", script)
        self.assertNotIn("Restart", script)

    def test_installer_failure_and_reinspection(self):
        backend = WindowsUsbBackend()
        result = SimpleNamespace(returncode=0, stdout='{"exitCode":1223}')
        with patch.object(windows, "powershell", return_value=result), \
                patch.object(backend, "inspect", return_value=replace(STATUS, tool="")) as inspect:
            with self.assertRaisesRegex(RuntimeError, "1223"):
                backend.install(STATUS)
            inspect.assert_called_once()

    def test_installer_nonzero_with_verified_installation(self):
        with patch.object(windows, "powershell", return_value=SimpleNamespace(returncode=0, stdout='{"exitCode":-1}')), \
                patch.object(WindowsUsbBackend, "inspect", return_value=STATUS):
            WindowsUsbBackend().install(STATUS)

    def test_installer_timeout(self):
        with patch.object(windows, "powershell", side_effect=subprocess.TimeoutExpired("installer", 900)), \
                self.assertRaises(subprocess.TimeoutExpired):
            WindowsUsbBackend().install(STATUS)

    def test_attach_lets_usbipd_select_distro_without_uac(self):
        with patch.object(windows, "powershell", return_value=SimpleNamespace(returncode=0, stdout='{"ok":true}')) as ps, \
                patch.object(windows, "elevated") as elevated:
            WindowsUsbBackend("My Ubuntu").tool_action(STATUS, BOARD, "attach")
        self.assertIn("attach --busid '2-3' --wsl", ps.call_args.args[0])
        self.assertNotIn("My Ubuntu", ps.call_args.args[0])
        self.assertIn("ForEach-Object { $_.ToString() }", ps.call_args.args[0])
        elevated.assert_not_called()

    def test_bind_uses_shared_uac_helper(self):
        with patch.object(windows, "elevated") as elevated, patch.object(windows, "powershell") as ps:
            WindowsUsbBackend().tool_action(STATUS, BOARD, "bind", elevated=True)
        self.assertIn("bind --busid '2-3'", elevated.call_args.args[0])
        ps.assert_not_called()

    def test_permission_helper_is_narrowly_targeted(self):
        with patch("os.access", side_effect=[False, True]), patch("os.getuid", return_value=1000), \
                patch.object(windows, "executable", return_value="wsl.exe"), \
                patch.object(windows, "run", return_value=SimpleNamespace(returncode=0)) as run:
            WindowsUsbBackend("Ubuntu").grant_access(PORT)
        args = run.call_args.args[0]
        self.assertEqual(args[:7], ["wsl.exe", "--distribution", "Ubuntu", "--user", "root", "--exec", "python3"])
        self.assertEqual(args[-5:], [PORT.address, "10c4", "ea60", "ABC", "1000"])

    def test_existing_permission_needs_no_root(self):
        with patch("os.access", return_value=True), patch.object(windows, "run") as run:
            WindowsUsbBackend().grant_access(PORT)
        run.assert_not_called()

    def test_permission_failure_is_reported(self):
        with patch("os.access", return_value=False), patch.object(windows, "executable", return_value="wsl.exe"), \
                patch.object(windows, "run", return_value=SimpleNamespace(returncode=1, stderr="denied")), \
                self.assertRaisesRegex(RuntimeError, "denied"):
            WindowsUsbBackend().grant_access(PORT)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.coordinator = UsbCoordinator(self.host)

    def tearDown(self):
        self.coordinator.shutdown()

    def attach(self, device=BOARD):
        self.assertTrue(self.coordinator.start("attach", device))
        self.assertEqual(finish(self.coordinator).state, "ready")

    def test_attach_verifies_permissions_and_owns_only_its_attachment(self):
        self.attach()
        self.assertEqual(self.coordinator.verified_port, PORT)
        self.assertEqual(self.host.granted, [PORT])
        self.assertTrue(self.coordinator.owned)
        self.assertEqual(self.host.actions, [("attach", "2-3", False)])

    def test_first_bind_then_attach(self):
        device = replace(BOARD, persisted_guid="")
        self.host.devices = (device,)
        self.attach(device)
        self.assertEqual(self.host.actions, [("bind", "2-3", True), ("attach", "2-3", False)])

    def test_preexisting_attachment_is_preserved_on_exit(self):
        self.host.devices = (replace(BOARD, attached=True),)
        self.host.ports = (PORT,)
        self.attach()
        self.coordinator.shutdown()
        self.assertFalse(self.host.actions)

    def test_normal_exit_detaches_owned_but_keeps_sharing(self):
        self.attach()
        self.coordinator.shutdown()
        self.assertEqual(self.host.actions[-1][0], "detach")
        self.assertTrue(self.host.devices[0].shared)

    def test_release_stops_recovery(self):
        self.attach()
        self.coordinator.start("release", BOARD)
        self.assertEqual(finish(self.coordinator).state, "released")
        self.assertIsNone(self.coordinator.session_device)
        self.assertFalse(self.coordinator.recovery_enabled or self.coordinator.owned)

    def test_unplugged_release_does_not_detach_other_board(self):
        self.attach()
        self.host.devices = ()
        self.coordinator.start("release", BOARD)
        self.assertEqual(finish(self.coordinator).state, "released")
        self.assertEqual([a[0] for a in self.host.actions], ["attach"])

    def test_recovery_refreshes_busid_path_and_permissions(self):
        self.attach()
        self.host.devices = (replace(BOARD, busid="4-7"),)
        self.host.ports = ()
        replacement = replace(PORT, address="/dev/ttyUSB2", node_id=(30, 190))
        self.host.next_ports = (replacement,)
        self.coordinator._next_retry = 0
        self.coordinator.poll()
        self.assertEqual(finish(self.coordinator).state, "ready")
        self.assertEqual(self.coordinator.session_device.linux_port, "/dev/ttyUSB2")
        self.assertEqual(self.host.actions[-1], ("attach", "4-7", False))
        self.assertEqual(self.host.granted, [PORT, replacement])

    def test_reused_path_is_reverified(self):
        self.attach()
        self.host.devices = (replace(BOARD, attached=True),)
        replacement = replace(PORT, node_id=(90, 188))
        self.host.ports = (replacement,)
        self.coordinator._next_retry = 0
        self.coordinator.poll()
        finish(self.coordinator)
        self.assertEqual(self.host.granted, [PORT, replacement])

    def test_unplugged_device_waits_without_reattaching_other_devices(self):
        self.host.devices = ()
        self.coordinator.start("attach", BOARD)
        self.assertEqual(finish(self.coordinator).state, "waiting")
        self.assertTrue(self.coordinator.recovery_enabled)
        self.assertFalse(self.host.actions)

    def test_changed_identity_pauses(self):
        self.host.devices = (replace(BOARD, instance_id="new-identity", persisted_guid="new-guid"),)
        self.coordinator.start("attach", BOARD)
        self.assertEqual(finish(self.coordinator).state, "selection_required")
        self.assertFalse(self.coordinator.recovery_enabled or self.host.actions)

    def test_duplicate_windows_identity_pauses(self):
        self.host.devices = (BOARD, BOARD)
        self.coordinator.start("attach", BOARD)
        self.assertEqual(finish(self.coordinator).state, "selection_required")
        self.assertFalse(self.host.actions)

    def test_ambiguous_linux_ports_require_selection(self):
        second = replace(PORT, address="/dev/ttyUSB1")
        self.host.next_ports = (PORT, second)
        self.coordinator.start("attach", BOARD)
        self.assertEqual(finish(self.coordinator).state, "choose_port")
        self.assertFalse(self.host.granted or self.coordinator.recovery_enabled)
        self.coordinator.start("attach", BOARD, second.address)
        self.assertEqual(finish(self.coordinator).state, "ready")
        self.assertEqual(self.host.granted, [second])

    def test_no_serial_matches_only_new_port(self):
        board = replace(BOARD, serial="")
        old = replace(PORT, address="/dev/ttyUSB8", serial="")
        new = replace(PORT, serial="")
        self.host.devices = (board,)
        self.host.ports = (old,)
        self.host.next_ports = (old, new)
        self.attach(board)
        self.assertEqual(self.host.granted, [new])

    def test_linux_enumeration_has_a_30_second_deadline(self):
        self.host.next_ports = ()
        with patch("studio.services.serial.wsl_usb.time.monotonic", side_effect=[100, 131]):
            result = self.coordinator._attach(BOARD)
        self.assertEqual(result.state, "waiting")
        self.assertFalse(self.host.granted)

    def test_recovery_never_triggers_uac(self):
        self.host.devices = (replace(BOARD, persisted_guid=""),)
        self.coordinator.start("attach", BOARD, recovery=True)
        self.assertEqual(finish(self.coordinator).state, "selection_required")
        self.assertFalse(self.host.actions)

    def test_uac_cancellation_stops_before_attach(self):
        self.host.devices = (replace(BOARD, persisted_guid=""),)
        with patch.object(self.host, "tool_action", side_effect=windows.AuthorizationCancelled("UAC cancelled")):
            self.coordinator.start("attach", BOARD)
            self.assertEqual(finish(self.coordinator).state, "cancelled")
        self.assertFalse(self.coordinator.recovery_enabled or self.coordinator.owned)

    def test_cancel_after_bind_preserves_sharing_and_stops_attach(self):
        self.host.devices = (replace(BOARD, persisted_guid=""),)
        original = self.host.tool_action
        def action(*args, **kwargs):
            original(*args, **kwargs)
            self.coordinator.cancel()
        with patch.object(self.host, "tool_action", side_effect=action):
            self.coordinator.start("attach", BOARD)
            self.assertEqual(finish(self.coordinator).state, "cancelled")
        self.assertEqual([a[0] for a in self.host.actions], ["bind"])
        self.assertTrue(self.host.devices[0].shared)

    def test_permission_failure_keeps_release_available(self):
        with patch.object(self.host, "grant_access", side_effect=RuntimeError("Permission denied")):
            self.coordinator.start("attach", BOARD)
            self.assertEqual(finish(self.coordinator).state, "failed")
        self.assertTrue(self.coordinator.session_device and self.coordinator.owned)

    def test_setup_timeout_is_visible(self):
        with patch.object(self.host, "inspect", side_effect=subprocess.TimeoutExpired("powershell", 30)):
            self.coordinator.start("inspect")
            self.assertEqual(finish(self.coordinator).state, "failed")
        self.assertIn("timed out", self.coordinator.status.detail)

    def test_install_reinspects_before_and_after(self):
        self.host.supported = False
        self.coordinator.start("install")
        self.assertTrue(finish(self.coordinator).supported)
        self.assertEqual(self.host.inspections, 2)
        self.assertFalse(self.host.actions)

    def test_flash_blocks_release_but_allows_usb_recovery(self):
        self.coordinator.flash_active = True
        self.assertFalse(self.coordinator.start("release", BOARD))
        self.assertFalse(self.coordinator.start("attach", BOARD))
        self.assertTrue(self.coordinator.start("attach", BOARD, recovery=True))
        finish(self.coordinator)

    def test_retry_delays_are_bounded(self):
        with patch("studio.services.serial.wsl_usb.time.monotonic", return_value=100):
            for delay in (1, 2, 5, 10, 10):
                self.coordinator.events.put(("result", replace(STATUS, state="waiting")))
                self.coordinator.poll()
                self.assertEqual(self.coordinator._next_retry, 100 + delay)

    def test_remembering_selection_does_not_attach_at_startup(self):
        self.coordinator.poll()
        self.assertFalse(self.host.actions or self.host.inspections)

    def test_shutdown_during_attach_still_detaches_completed_attachment(self):
        import threading
        entered, resume = threading.Event(), threading.Event()
        original = self.host.tool_action
        def action(status, device, operation, **kwargs):
            if operation == "attach":
                entered.set()
                resume.wait(1)
            original(status, device, operation, **kwargs)
        with patch.object(self.host, "tool_action", side_effect=action):
            self.coordinator.start("attach", BOARD)
            self.assertTrue(entered.wait(1))
            timer = threading.Timer(0.01, resume.set)
            timer.start()
            self.coordinator.shutdown()
            timer.join()
        self.assertEqual([a[0] for a in self.host.actions], ["attach", "detach"])
        self.assertFalse(self.coordinator.owned)


class PermissionScriptTests(unittest.TestCase):
    def test_non_usb_path_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "USB serial"):
            permissions.usb_identity("/etc/passwd")

    def test_regular_file_is_rejected(self):
        with patch.object(permissions.os, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFREG)), \
                self.assertRaisesRegex(ValueError, "device node"):
            permissions.grant(PORT.address, PORT.vid, PORT.pid, PORT.serial, 1000)

    def test_wrong_usb_identity_is_rejected(self):
        with patch.object(permissions.os, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFCHR)), \
                patch.object(permissions, "usb_identity", return_value=("ffff", "ffff", "ABC")), \
                self.assertRaisesRegex(ValueError, "identity changed"):
            permissions.grant(PORT.address, PORT.vid, PORT.pid, PORT.serial, 1000)

    def test_grants_only_owner_access_on_verified_descriptor(self):
        node = SimpleNamespace(st_mode=stat.S_IFCHR | 0o620, st_ino=10, st_rdev=188)
        with patch.object(permissions.os, "lstat", return_value=node), \
                patch.object(permissions, "usb_identity", return_value=(PORT.vid, PORT.pid, PORT.serial)), \
                patch.object(permissions.os, "open", return_value=5), patch.object(permissions.os, "fstat", return_value=node), \
                patch.object(permissions.os, "fchown") as chown, patch.object(permissions.os, "fchmod") as chmod, \
                patch.object(permissions.os, "close") as close:
            permissions.grant(PORT.address, PORT.vid, PORT.pid, PORT.serial, 1000)
        chown.assert_called_once_with(5, 1000, -1)
        chmod.assert_called_once_with(5, 0o620)
        close.assert_called_once_with(5)


if __name__ == "__main__":
    unittest.main()
