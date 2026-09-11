"""WSL-only USB setup UI and shared monitor/upload session integration."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QProcess, QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from ...services.serial.wsl_usb import INSTALL_URL, UsbCoordinator, same_device
from ...storage.keys import WSL_USB_SELECTION
from ...storage.settings import settings


class WslUsbDialog(QDialog):
    def __init__(self, integration, parent):
        super().__init__(parent)
        self.integration = integration
        self.setWindowTitle("Windows USB access")
        self.setMinimumWidth(660)
        layout = QVBoxLayout(self)
        note = QLabel("Use a Windows USB board in Studio's WSL session. Windows cannot use it while attached. "
                      "Choose Release to Windows when finished.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.devices = QComboBox()
        self.devices.setMinimumContentsLength(45)
        self.devices.currentIndexChanged.connect(self._selection_changed)
        layout.addWidget(self.devices)
        self.all_devices = QCheckBox("Show all USB devices (including unrecognized boards)")
        self.all_devices.toggled.connect(self.render)
        layout.addWidget(self.all_devices)
        self.identity = QLabel()
        self.identity.setTextFormat(Qt.PlainText)
        self.identity.setWordWrap(True)
        self.identity.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.identity)
        self.linux_ports = QComboBox()
        layout.addWidget(self.linux_ports)
        guide = QLabel(f'<a href="{INSTALL_URL}">Official Windows USB installation and troubleshooting guide</a>')
        guide.setOpenExternalLinks(True)
        layout.addWidget(guide)
        row = QHBoxLayout()
        self.refresh_button = self._button(row, "Refresh", lambda: integration.action("inspect"))
        self.install_button = self._button(row, "Install USB support", lambda: integration.action("install"))
        self.attach_button = self._button(row, "Use in Studio", self.attach)
        self.release_button = self._button(row, "Release to Windows", integration.release)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.cancel_button = self._button(row, "Cancel setup", self.cancel)
        row.addStretch()
        self._button(row, "Close", self.reject)
        layout.addLayout(row)
        self.render()

    @staticmethod
    def _button(row, title, action):
        button = QPushButton(title)
        button.clicked.connect(action)
        row.addWidget(button)
        return button

    def _selection_changed(self):
        device = self.devices.currentData()
        self.identity.setText((f"USB {device.vid or '?'}:{device.pid or '?'}   COM: {device.com_port or '—'}   "
                               f"BUSID: {device.busid or 'unplugged'}\nIdentity: {device.identity}\n"
                               f"{'Attached' if device.attached else 'Shared' if device.shared else 'Not shared'}"
                               f"   Linux: {device.linux_port or 'not verified'}") if device else "Select the board before attaching.")
        coordinator = self.integration.coordinator
        self.attach_button.setEnabled(bool(device and coordinator.status.supported
                                           and not coordinator.running and not coordinator.flash_active))

    def render(self, *_):
        coordinator = self.integration.coordinator
        status = coordinator.status
        self.status.setText("\n".join(s for s in (status.message, status.detail) if s))
        previous = self.devices.currentData()
        remembered = self.integration.preferences.get(WSL_USB_SELECTION)
        self.devices.blockSignals(True)
        self.devices.clear()
        self.devices.addItem("Select a Windows USB device…", None)
        for device in status.devices:
            if self.all_devices.isChecked() or device.candidate:
                label = f"{device.description} — {device.com_port or device.busid or 'unplugged'}"
                self.devices.addItem(label, device)
                if (previous and same_device(device, previous)) or (not previous and device.identity == remembered):
                    self.devices.setCurrentIndex(self.devices.count() - 1)
        self.devices.blockSignals(False)
        chosen = self.linux_ports.currentData()
        self.linux_ports.clear()
        self.linux_ports.addItem("Select the matching Linux serial port…", "")
        if status.state == "choose_port":
            for port in status.ports:
                self.linux_ports.addItem(port.address, port.address)
            self.linux_ports.setCurrentIndex(max(0, self.linux_ports.findData(chosen)))
        self.linux_ports.setVisible(status.state == "choose_port")
        self.devices.setEnabled(not coordinator.running and not coordinator.flash_active)
        self.refresh_button.setEnabled(not coordinator.running)
        self.install_button.setVisible(not status.supported)
        self.install_button.setEnabled(bool(status.winget and not coordinator.running and not coordinator.flash_active))
        self.release_button.setEnabled(bool(coordinator.session_device and not coordinator.running and not coordinator.flash_active))
        self.cancel_button.setEnabled(coordinator.running or coordinator.recovery_enabled)
        self._selection_changed()

    def attach(self):
        coordinator = self.integration.coordinator
        device = self.devices.currentData()
        if coordinator.status.state == "choose_port":
            if not self.linux_ports.currentData():
                self.status.setText("Select the matching Linux serial port before continuing.")
                return
            device = coordinator.status.selected
        if device:
            self.integration.action("attach", device, self.linux_ports.currentData() or "")

    def cancel(self):
        self.integration.coordinator.cancel()
        self.integration._render()

    def reject(self):
        if self.integration.coordinator.running:
            self.integration.coordinator.cancel()
        super().reject()


class WslSerialIntegration(QObject):
    """One instance per Debug Tray; native platforms never construct this."""
    def __init__(self, monitor, firmware, parent, *, coordinator=None, preferences=None):
        super().__init__(parent)
        self.monitor = monitor
        self.firmware = firmware
        self.coordinator = coordinator or UsbCoordinator()
        self.preferences = preferences or settings()
        self.dialog = None
        self._closing = False
        self._resume_monitor = False
        self._last_applied = ""
        self._next_monitor_retry = 0.0
        self.labels = []
        self.buttons = []
        for tab in (monitor, firmware):
            row = QHBoxLayout()
            button = QPushButton("Windows USB…")
            button.setObjectName("DebugAction")
            button.clicked.connect(self.open)
            row.addWidget(button)
            label = QLabel("Windows USB boards need to be attached to WSL first.")
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            row.addWidget(label, 1)
            if tab is monitor:
                self.cancel_reconnect = QPushButton("Cancel monitor reconnect")
                self.cancel_reconnect.clicked.connect(lambda: monitor.disconnect())
                self.cancel_reconnect.hide()
                row.addWidget(self.cancel_reconnect)
            tab.layout().insertLayout(0, row)
            tab.empty_scan.connect(self._empty_scan)
            self.labels.append(label)
            self.buttons.append(button)
        monitor.connection_opened.connect(self._opened)
        monitor.connection_closed.connect(self._closed)
        firmware.flash_activity.connect(self._flash_activity)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def _empty_scan(self):
        self._last_applied = ""
        for label in self.labels:
            label.setText("No Linux USB port found. Open Windows USB… to attach your board.")

    def open(self):
        if self.dialog is None:
            self.dialog = WslUsbDialog(self, self.parent())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        self.action("inspect")

    def action(self, action, device=None, linux_port=""):
        started = self.coordinator.start(action, device, linux_port)
        if started and action == "attach":
            self.preferences.set(WSL_USB_SELECTION, device.identity)
        self._render()
        return started

    def release(self):
        coordinator = self.coordinator
        if coordinator.running or coordinator.flash_active or not coordinator.session_device:
            return
        self.monitor.disconnect()
        self._resume_monitor = False
        self.action("release", coordinator.session_device)

    def _opened(self, address):
        device = self.coordinator.session_device
        self._resume_monitor = bool(device and device.linux_port == address and self.coordinator.recovery_enabled)

    def _closed(self, reason):
        if reason == "manual":
            self._resume_monitor = False
        elif reason == "lost":
            self._next_monitor_retry = time.monotonic() + 1

    def _flash_activity(self, active):
        self.coordinator.flash_active = active
        self.monitor.set_port_reserved(active)
        if not active and not self._closing:
            self._last_applied = ""
            self.poll()
        self._render()

    @staticmethod
    def _select_port(picker, address):
        index = picker.findData(address)
        if index < 0:
            picker.addItem(f"{address} — Windows USB", address)
            index = picker.count() - 1
        picker.setCurrentIndex(index)

    def poll(self):
        if self._closing:
            return
        coordinator = self.coordinator
        changed = coordinator.poll()
        status = coordinator.status
        device = coordinator.session_device
        self._sync_availability()
        if status.state == "released" and self._last_applied:
            for picker in (self.monitor.port, self.firmware.port):
                index = picker.findData(self._last_applied)
                if index >= 0:
                    picker.removeItem(index)
                if picker.currentText() == self._last_applied:
                    picker.setEditText("")
            self._last_applied = ""
        if coordinator.verified_port and device and not coordinator.running:
            address = device.linux_port
            if address != self._last_applied and not coordinator.flash_active:
                if not self.monitor.serial.isOpen():
                    self._select_port(self.monitor.port, address)
                if self.firmware._process.state() == QProcess.NotRunning:
                    self._select_port(self.firmware.port, address)
                    self._last_applied = address
                self.preferences.set(WSL_USB_SELECTION, device.identity)
            if (self._resume_monitor and coordinator.recovery_enabled and not coordinator.flash_active
                    and not self.monitor.serial.isOpen() and time.monotonic() >= self._next_monitor_retry):
                self._select_port(self.monitor.port, address)
                self.monitor.connect()
                self._next_monitor_retry = time.monotonic() + 2
        elif (not coordinator.verified_port and coordinator.recovery_enabled and device
              and self._resume_monitor and self.monitor.serial.isOpen()):
            # A disappearing node may not produce QSerialPort's error until
            # the next read. Preserve intent while closing the stale handle.
            self.monitor.disconnect(for_flash=True)
        if changed:
            self._render()
        self.cancel_reconnect.setVisible(self._resume_monitor and not self.monitor.serial.isOpen())

    def _render(self):
        coordinator = self.coordinator
        for label in self.labels:
            label.setText(coordinator.status.message)
            label.setToolTip(coordinator.status.detail)
        self._sync_availability()
        if self.dialog:
            self.dialog.render()

    def _sync_availability(self):
        coordinator = self.coordinator
        unavailable = bool(coordinator.session_device and not coordinator.verified_port)
        self.firmware.set_port_setup_busy(coordinator.running or unavailable)
        self.monitor.set_port_reserved(coordinator.flash_active or unavailable)

    def begin_shutdown(self):
        self._closing = True
        self._resume_monitor = False
        self.timer.stop()

    def shutdown(self):
        self.begin_shutdown()
        self.coordinator.shutdown()
