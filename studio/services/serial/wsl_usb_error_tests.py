"""Regressions for failed USB/IP handoff and stale Windows device records."""
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from .wsl_usb import UsbCoordinator, WindowsUsbBackend, windows
from .wsl_usb_errors import UsbOperationError
from .wsl_usb_tests import BOARD, STATUS, Host, finish

OUTPUT = ('usbipd: info: Selecting a specific distribution is no longer required.\n'
          'WSL usbip: error: Attach Request for 1-3 failed - Device in error state\n'
          "usbipd: error: Failed to attach device with busid '1-3'.")


class UsbFailureTests(unittest.TestCase):
    def test_device_error_shows_recovery_instead_of_information(self):
        error = UsbOperationError('attach', OUTPUT)
        self.assertIn('device in error state', error.message)
        self.assertIn('UART/CH340', error.detail)
        self.assertIn('Unplug and reconnect', error.detail)
        self.assertNotIn('Selecting a specific distribution', error.detail)
        self.assertIn('Attach Request for 1-3 failed', error.detail)
        self.assertEqual(error.output, OUTPUT)

    def test_native_failure_is_classified(self):
        import json
        with patch.object(windows, 'powershell', return_value=SimpleNamespace(
                returncode=0, stdout=json.dumps({'error': OUTPUT}))):
            with self.assertRaises(UsbOperationError) as result:
                WindowsUsbBackend('Ubuntu').tool_action(STATUS, BOARD, 'attach')
        self.assertIn('device in error state', result.exception.message)

    def test_failed_attachment_reinspects_changed_device(self):
        host = Host()
        coordinator = UsbCoordinator(host)
        def fail(*args, **kwargs):
            host.devices = (replace(BOARD, busid='', attached=False),)
            raise UsbOperationError('attach', OUTPUT)
        with patch.object(host, 'tool_action', side_effect=fail):
            coordinator.start('attach', BOARD)
            status = finish(coordinator)
        self.assertEqual(status.state, 'failed')
        self.assertEqual(status.devices[0].busid, '')
        self.assertEqual(status.selected.busid, '')
        self.assertFalse(coordinator.recovery_enabled)
        self.assertEqual(host.inspections, 2)
        coordinator.shutdown()

    def test_failed_refresh_preserves_original_error(self):
        host = Host()
        coordinator = UsbCoordinator(host)
        with patch.object(host, 'inspect', side_effect=[STATUS, RuntimeError('Windows unavailable')]), \
                patch.object(host, 'tool_action', side_effect=UsbOperationError('attach', OUTPUT)):
            coordinator.start('attach', BOARD)
            status = finish(coordinator)
        self.assertIn('device in error state', status.message)
        self.assertNotIn('Windows unavailable', status.detail)
        coordinator.shutdown()


if __name__ == '__main__':
    unittest.main()
