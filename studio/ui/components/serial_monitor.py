"""Live USB serial console for ESP32 firmware output."""

from __future__ import annotations

import codecs
import json
import os
import time
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtSerialPort import QSerialPort, QSerialPortInfo
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...models.config.mqtt_users import users
from ...services.firmware import mqtt_provisioning_command, wifi_provisioning_command
from ...services.network import active_firewall, our_port


class SerialMonitor(QWidget):
    """Owns one serial connection and renders its expected errors locally."""

    # How long without a SerialHeartbeat line before the board reads as gone
    # rather than merely quiet — a little over the firmware's own 2s
    # interval, so one skipped line from USB jitter does not flip the chip.
    HEARTBEAT_STALE_S = 5.0

    def __init__(self, *, broker_host: Callable[[], str] = lambda: "") -> None:
        super().__init__()
        self._broker_host = broker_host
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._incoming_lines = ""
        self.serial = QSerialPort(self)
        self.serial.readyRead.connect(self._read)
        self.serial.errorOccurred.connect(self._serial_error)

        # Last SerialHeartbeat.cpp line seen, and when — None until one
        # arrives, so "never heard from the board" and "used to, not lately"
        # read differently.
        self._last_heartbeat: dict | None = None
        self._last_heartbeat_at: float = 0.0
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(1000)
        self._heartbeat_timer.timeout.connect(self._refresh_board_status)
        self._heartbeat_timer.start()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 0)
        layout.setSpacing(5)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("USB port"))
        self.port = QComboBox()
        self.port.setObjectName("SerialPort")
        self.port.setEditable(True)
        self.port.setMinimumWidth(180)
        self.port.lineEdit().setPlaceholderText("Connect a board, then scan")
        controls.addWidget(self.port, 1)
        self.scan_button = self._button("List USB Devices", self.scan)
        controls.addWidget(self.scan_button)
        controls.addSpacing(8)
        controls.addWidget(QLabel("Baud"))
        self.baud = QComboBox()
        self.baud.setEditable(True)
        # 115200 is the only rate the firmware actually runs at
        # (Serial.begin(115200) in firmware.ino) — the others are here only
        # because a USB-CDC bridge will happily "connect" at any of them and
        # then decode garbage, which reads as a broken board rather than a
        # wrong setting. Listed as an escape hatch for other firmware, not a
        # real choice for this one.
        self.baud.addItems(["★ 115200 (Desk Buddy)", "921600", "460800", "230400", "57600", "9600"])
        self.baud.setCurrentIndex(0)
        controls.addWidget(self.baud)
        self.connect_button = self._button("Connect", self.toggle_connection)
        controls.addWidget(self.connect_button)
        layout.addLayout(controls)

        actions = QHBoxLayout()
        self.status = QLabel("Disconnected")
        self.status.setObjectName("DebugStatus")
        actions.addWidget(self.status)
        self.board_status = QLabel()
        self.board_status.setObjectName("DebugStatus")
        actions.addWidget(self.board_status)
        actions.addStretch(1)
        self.autoscroll = QCheckBox("Auto-scroll")
        self.autoscroll.setChecked(True)
        actions.addWidget(self.autoscroll)
        self.wrap_lines = QCheckBox("Wrap")
        self.wrap_lines.toggled.connect(self._set_wrap)
        actions.addWidget(self.wrap_lines)
        actions.addWidget(self._button("Copy", self.copy_output))
        actions.addWidget(self._button("Clear", self.clear_output))
        layout.addLayout(actions)

        provisioning_row = QHBoxLayout()
        self.wifi_button = self._button("Set Wi-Fi…", self.open_wifi_dialog)
        provisioning_row.addWidget(self.wifi_button)
        self.mqtt_button = self._button("Set MQTT…", self.open_mqtt_dialog)
        provisioning_row.addWidget(self.mqtt_button)
        provisioning_row.addStretch(1)
        layout.addLayout(provisioning_row)

        self.output = QPlainTextEdit()
        self.output.setObjectName("DebugLog")
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.output.setPlaceholderText(
            "Connect to watch ESP32 boot messages, firmware logs, and serial errors."
        )
        self.output.document().setMaximumBlockCount(20_000)
        layout.addWidget(self.output, 1)

        send_row = QHBoxLayout()
        self.send_text = QLineEdit()
        self.send_text.setObjectName("SerialSend")
        self.send_text.setPlaceholderText("Send text to the ESP32")
        self.send_text.returnPressed.connect(self.send)
        send_row.addWidget(self.send_text, 1)
        self.line_ending = QComboBox()
        self.line_ending.addItem("Newline (LF)", "\n")
        self.line_ending.addItem("Both (CRLF)", "\r\n")
        self.line_ending.addItem("No line ending", "")
        send_row.addWidget(self.line_ending)
        self.send_button = self._button("Send", self.send)
        send_row.addWidget(self.send_button)
        layout.addLayout(send_row)
        self._set_connected(False)

    def _set_wrap(self, enabled: bool) -> None:
        self.output.setLineWrapMode(
            QPlainTextEdit.WidgetWidth if enabled else QPlainTextEdit.NoWrap
        )

    @staticmethod
    def _button(label: str, action: Callable[[], None]) -> QPushButton:
        button = QPushButton(label)
        button.setObjectName("DebugAction")
        button.clicked.connect(action)
        return button

    @staticmethod
    def _is_usb(info: QSerialPortInfo) -> bool:
        if info.hasVendorIdentifier() or info.hasProductIdentifier():
            return True
        name = info.systemLocation().lower()
        if os.name == "nt":
            return info.portName().lower().startswith("com")
        return any(
            marker in name
            for marker in ("ttyusb", "ttyacm", "cu.usb", "tty.usb", "cu.slab", "cu.wch")
        )

    @property
    def selected_port(self) -> str:
        text = self.port.currentText().strip()
        index = self.port.currentIndex()
        if index >= 0 and text == self.port.itemText(index):
            return str(self.port.itemData(index) or text)
        return text.split(" — ", 1)[0]

    def scan(self) -> None:
        previous = self.selected_port
        ports = [info for info in QSerialPortInfo.availablePorts() if self._is_usb(info)]
        self.port.clear()
        for info in ports:
            address = info.systemLocation() or info.portName()
            details = info.description() or info.manufacturer() or "USB serial device"
            self.port.addItem(f"{address} — {details}", address)

        if previous:
            match = self.port.findData(previous)
            if match >= 0:
                self.port.setCurrentIndex(match)
            elif not ports:
                self.port.setEditText(previous)

        if ports:
            self._append_line(
                f"Found {len(ports)} USB serial device{'s' if len(ports) != 1 else ''}:"
            )
            for index in range(self.port.count()):
                self._append_line(f"  {self.port.itemText(index)}")
        else:
            self._append_line(
                "No USB serial devices found. Connect the ESP32-S3-CAM and scan again."
            )

    def toggle_connection(self) -> None:
        if self.serial.isOpen():
            self.disconnect()
        else:
            self.connect()

    def connect(self) -> None:
        port = self.selected_port
        if not port:
            self._append_line("ERROR: Select a USB port before connecting.")
            self.status.setText("No USB port selected")
            return
        digits = "".join(character for character in self.baud.currentText() if character.isdigit())
        try:
            # Only the digits matter — the current item may carry a "★" or a
            # "(Desk Buddy)" annotation the port name never will.
            baud = int(digits)
        except ValueError:
            self._append_line("ERROR: Baud rate must be a whole number.")
            self.status.setText("Invalid baud rate")
            return
        if baud <= 0:
            self._append_line("ERROR: Baud rate must be greater than zero.")
            self.status.setText("Invalid baud rate")
            return

        self.serial.setPortName(port)
        self.serial.setBaudRate(baud)
        self.serial.setDataBits(QSerialPort.Data8)
        self.serial.setParity(QSerialPort.NoParity)
        self.serial.setStopBits(QSerialPort.OneStop)
        self.serial.setFlowControl(QSerialPort.NoFlowControl)
        if not self.serial.open(QSerialPort.ReadWrite):
            self._append_line(f"ERROR: Could not open {port}: {self.serial.errorString()}")
            self.status.setText("Connection failed")
            return

        self._decoder.reset()
        self._incoming_lines = ""
        self._last_heartbeat = None
        self._set_connected(True)
        self.status.setText(f"Connected to {port} at {baud:,} baud")
        self._append_line(f"\nConnected to {port} at {baud:,} baud.")
        self._refresh_board_status()

    def disconnect(self, *, for_flash: bool = False) -> None:
        if not self.serial.isOpen():
            return
        port = self.serial.portName()
        self.serial.close()
        self._set_connected(False)
        self.status.setText("Disconnected")
        self._last_heartbeat = None
        self._refresh_board_status()
        reason = " for firmware flash" if for_flash else ""
        self._append_line(f"Disconnected from {port}{reason}.")

    def disconnect_for_flash(self, _port: str = "") -> None:
        self.disconnect(for_flash=True)

    def _set_connected(self, connected: bool) -> None:
        self.connect_button.setText("Disconnect" if connected else "Connect")
        self.port.setEnabled(not connected)
        self.baud.setEnabled(not connected)
        self.scan_button.setEnabled(not connected)
        self.send_text.setEnabled(connected)
        self.line_ending.setEnabled(connected)
        self.send_button.setEnabled(connected)
        self.wifi_button.setEnabled(connected)
        self.mqtt_button.setEnabled(connected)

    def _read(self) -> None:
        data = bytes(self.serial.readAll())
        text = self._decoder.decode(data)
        self._append_text(text)
        self._inspect_protocol_lines(text)

    def _inspect_protocol_lines(self, text: str) -> None:
        self._incoming_lines = (self._incoming_lines + text)[-4096:]
        while "\n" in self._incoming_lines:
            line, self._incoming_lines = self._incoming_lines.split("\n", 1)
            try:
                response = json.loads(line.strip())
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

            if response.get("serial_heartbeat") is True:
                self._last_heartbeat = response
                self._last_heartbeat_at = time.monotonic()
                self._refresh_board_status()
                continue

            kind = response.get("serial_provisioning")
            if kind not in ("wifi", "mqtt"):
                continue
            label = "Wi-Fi" if kind == "wifi" else "MQTT settings"
            if response.get("status") == "saved":
                self.status.setText(f"{label} saved; robot restarting…")
            elif response.get("status") == "error":
                detail = str(response.get("error") or f"Robot rejected {label}")
                self.status.setText(f"{label} setup failed: {detail}")

    def _refresh_board_status(self) -> None:
        """The board chip: alive/stale/silent, from SerialHeartbeat.cpp lines.

        Ticks on a timer as well as on each new line, so a board that stops
        sending — unplugged, crashed, held in reset — visibly goes stale
        instead of the chip freezing on the last good reading forever.
        """
        if not self.serial.isOpen() or self._last_heartbeat is None:
            self.board_status.setText("")
            return

        age = time.monotonic() - self._last_heartbeat_at
        if age > self.HEARTBEAT_STALE_S:
            self.board_status.setText(f"○ no heartbeat ({age:.0f}s)")
            return

        wifi = "wifi up" if self._last_heartbeat.get("wifi_connected") else "wifi down"
        self.board_status.setText(f"● board alive · {wifi}")

    def send(self) -> None:
        if not self.serial.isOpen():
            self._append_line("ERROR: Connect the serial monitor before sending.")
            return
        text = self.send_text.text()
        if not text:
            return
        ending = str(self.line_ending.currentData())
        written = self.serial.write((text + ending).encode("utf-8"))
        if written < 0:
            self._append_line(f"ERROR: Could not write to serial port: {self.serial.errorString()}")
            return
        self.send_text.clear()

    def open_wifi_dialog(self) -> None:
        if not self.serial.isOpen():
            self._append_line("ERROR: Connect the serial monitor before setting Wi-Fi.")
            return
        dialog = WifiDialog(self, on_save=self._send_wifi)
        dialog.exec()

    def _send_wifi(self, ssid: str, password: str) -> str | None:
        """Validate and send one Wi-Fi command.

        "" on success (the dialog closes), a message on a real failure (the
        dialog shows it and stays open to retry), or None if the user backed
        out of the confirmation — not an error, just staying on the dialog
        with what they already typed.
        """
        try:
            command = wifi_provisioning_command(ssid, password)
        except ValueError as error:
            return str(error)

        answer = QMessageBox.question(
            self,
            "Update robot Wi-Fi?",
            f'Save network “{ssid}” on the robot and restart it?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return None

        written = self.serial.write(command)
        if written < 0:
            error = self.serial.errorString()
            self._append_line(f"ERROR: Could not send Wi-Fi settings: {error}")
            self.status.setText("Wi-Fi setup failed")
            return f"Could not send Wi-Fi settings: {error}"
        self.serial.flush()
        self.status.setText("Saving Wi-Fi; waiting for robot restart…")
        self._append_line(
            f'Sent Wi-Fi setup for “{ssid}”. Password omitted. The robot will restart.'
        )
        return ""

    def open_mqtt_dialog(self) -> None:
        if not self.serial.isOpen():
            self._append_line("ERROR: Connect the serial monitor before setting MQTT.")
            return
        dialog = MqttDialog(
            self,
            accounts=users().all(),
            broker_host=self._broker_host(),
            broker_port=our_port(),
            on_save=self._send_mqtt,
        )
        dialog.exec()

    def _send_mqtt(
        self, server: str, port_text: str, user: str, password: str, client_id: str,
        tls: bool | None = None,
    ) -> str | None:
        """Validate and send one MQTT command. Same three-way contract as
        _send_wifi: "" success, a message on failure, None on user cancel."""
        try:
            port = int(port_text) if port_text.strip() else 0
        except ValueError:
            return "Port must be a whole number."

        try:
            command = mqtt_provisioning_command(
                server, port, user, password, client_id, tls
            )
        except ValueError as error:
            return str(error)

        answer = QMessageBox.question(
            self,
            "Update robot MQTT settings?",
            f'Point the robot at “{server}” and restart it?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return None

        written = self.serial.write(command)
        if written < 0:
            error = self.serial.errorString()
            self._append_line(f"ERROR: Could not send MQTT settings: {error}")
            self.status.setText("MQTT setup failed")
            return f"Could not send MQTT settings: {error}"
        self.serial.flush()
        self.status.setText("Saving MQTT settings; waiting for robot restart…")
        self._append_line(
            f'Sent MQTT setup for “{server}”. Password omitted. The robot will restart.'
        )
        return ""

    def _serial_error(self, error) -> None:
        if error == QSerialPort.NoError:
            return
        # open() reports its own contextual error after returning false.
        if not self.serial.isOpen():
            return
        self._append_line(f"\nSERIAL ERROR: {self.serial.errorString()}")
        self.status.setText("Serial connection error")
        if error in (QSerialPort.ResourceError, QSerialPort.DeviceNotFoundError):
            self.serial.close()
            self._set_connected(False)

    def _append_text(self, text: str) -> None:
        if not text:
            return
        cursor = QTextCursor(self.output.document())
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        if self.autoscroll.isChecked():
            self.output.setTextCursor(cursor)
            self.output.ensureCursorVisible()

    def _append_line(self, text: str) -> None:
        existing = self.output.toPlainText()
        separator = "" if not existing or existing.endswith("\n") else "\n"
        self._append_text(separator + text + "\n")

    def copy_output(self) -> None:
        text = self.output.toPlainText()
        if text:
            QApplication.clipboard().setText(text)

    def clear_output(self) -> None:
        self.output.clear()

    def shutdown(self) -> None:
        if self.serial.isOpen():
            self.serial.close()


class WifiDialog(QDialog):
    """SSID and password, asked for only while actually setting Wi-Fi.

    A modal rather than a row that sits on the tab permanently: setting a
    robot's Wi-Fi is a one-off task done once per network change, not
    something that deserves two fields' worth of always-visible chrome next
    to controls used on every connection.
    """

    def __init__(self, parent: QWidget, *, on_save) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set Robot Wi-Fi")
        self.setModal(True)
        self._on_save = on_save

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        intro = QLabel("Sent over USB serial to the robot currently connected.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.ssid = QLineEdit()
        self.ssid.setObjectName("SerialCredential")
        self.ssid.setPlaceholderText("Network name")
        self.ssid.setMaxLength(32)
        layout.addWidget(QLabel("Network name"))
        layout.addWidget(self.ssid)

        self.password = QLineEdit()
        self.password.setObjectName("SerialPassword")
        self.password.setPlaceholderText("Password")
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setMaxLength(64)
        self.password.returnPressed.connect(self.save)
        layout.addWidget(QLabel("Password"))

        password_row = QHBoxLayout()
        password_row.setSpacing(0)
        password_row.addWidget(self.password, 1)
        self.password_toggle = QToolButton()
        self.password_toggle.setObjectName("PasswordEye")
        self.password_toggle.setText("👁")
        self.password_toggle.setCheckable(True)
        self.password_toggle.setCursor(Qt.PointingHandCursor)
        self.password_toggle.setToolTip("Show password")
        self.password_toggle.setAccessibleName("Show password")
        self.password_toggle.toggled.connect(self._set_password_visible)
        password_row.addWidget(self.password_toggle)
        layout.addLayout(password_row)

        self.error = QLabel()
        self.error.setObjectName("FieldError")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)

        buttons = QHBoxLayout()
        save = QPushButton("Save Wi-Fi & Restart")
        save.setObjectName("ContextPrimary")
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self.save)
        buttons.addWidget(save)

        cancel = QPushButton("Cancel")
        cancel.setObjectName("ContextAction")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

        self.ssid.setFocus()

    def _set_password_visible(self, visible: bool) -> None:
        self.password.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)
        label = "Hide password" if visible else "Show password"
        self.password_toggle.setToolTip(label)
        self.password_toggle.setAccessibleName(label)

    def save(self) -> None:
        error = self._on_save(self.ssid.text(), self.password.text())
        if error is None:
            # The user backed out of the confirmation — stay open with what
            # they typed rather than treat it as a failure to report.
            return
        if error:
            self.error.setText(error)
            self.error.show()
            self.password.clear()
            self.password_toggle.setChecked(False)
            self.password.setFocus()
            return
        self.accept()


CUSTOM_ACCOUNT = "Custom…"

# Addresses that only ever mean "this machine". Sending one to a robot points
# it at itself; it is never a usable broker address for a separate device.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", ""})


class MqttDialog(QDialog):
    """Broker address and credentials, asked for only while setting MQTT.

    Same one-off-task reasoning as WifiDialog: this is filled in once per
    broker change, not something that earns permanent space on the tab.

    Studio already knows every account it manages — server, port, username,
    password — so picking one here is a click, not five fields to retype.
    Only "Custom…" opens the fields up, for a broker Studio does not manage
    (a cloud broker, `mqtt.deskbuddy.ai`, someone else's Mosquitto). Every
    field but the server is optional on the wire either way — see
    mqtt_provisioning_command — so an empty user/password/client ID in
    Custom mode just leaves whatever the robot already has untouched.
    """

    def __init__(self, parent: QWidget, *, accounts, broker_host: str,
                 broker_port: int, on_save) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set Robot MQTT")
        self.setModal(True)
        self._on_save = on_save
        self._accounts = {account.name: account for account in accounts}
        self._broker_host = broker_host
        self._broker_port = broker_port

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        intro = QLabel(
            "Sent over USB serial to the robot currently connected."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        layout.addWidget(QLabel("User"))
        self.account = QComboBox()
        self.account.addItems([*self._accounts.keys(), CUSTOM_ACCOUNT])
        self.account.currentTextChanged.connect(self._account_changed)
        layout.addWidget(self.account)

        # Carries both blocking problems and non-blocking advice, so its
        # style is set per message rather than fixed here.
        self.broker_note = QLabel()
        self.broker_note.setWordWrap(True)
        self.broker_note.hide()
        layout.addWidget(self.broker_note)

        self.server = QLineEdit()
        self.server.setObjectName("SerialCredential")
        self.server.setPlaceholderText("mqtt.deskbuddy.ai")
        layout.addWidget(QLabel("Broker address"))
        layout.addWidget(self.server)

        self.port = QLineEdit()
        self.port.setObjectName("SerialCredential")
        self.port.setPlaceholderText("8883")
        layout.addWidget(QLabel("Port"))
        layout.addWidget(self.port)

        self.user = QLineEdit()
        self.user.setObjectName("SerialCredential")
        self.user.setPlaceholderText("Username (leave blank to keep current)")
        layout.addWidget(QLabel("Username"))
        layout.addWidget(self.user)

        self.password = QLineEdit()
        self.password.setObjectName("SerialPassword")
        self.password.setPlaceholderText("Password (leave blank to keep current)")
        self.password.setEchoMode(QLineEdit.Password)
        self.password.returnPressed.connect(self.save)
        layout.addWidget(QLabel("Password"))

        password_row = QHBoxLayout()
        password_row.setSpacing(0)
        password_row.addWidget(self.password, 1)
        self.password_toggle = QToolButton()
        self.password_toggle.setObjectName("PasswordEye")
        self.password_toggle.setText("👁")
        self.password_toggle.setCheckable(True)
        self.password_toggle.setCursor(Qt.PointingHandCursor)
        self.password_toggle.setToolTip("Show password")
        self.password_toggle.setAccessibleName("Show password")
        self.password_toggle.toggled.connect(self._set_password_visible)
        password_row.addWidget(self.password_toggle)
        layout.addLayout(password_row)

        self.client_id = QLineEdit()
        self.client_id.setObjectName("SerialCredential")
        self.client_id.setPlaceholderText("Client ID (leave blank to keep current)")
        layout.addWidget(QLabel("Client ID"))
        layout.addWidget(self.client_id)

        self.error = QLabel()
        self.error.setObjectName("FieldError")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)

        buttons = QHBoxLayout()
        self.save_button = QPushButton("Save MQTT & Restart")
        self.save_button.setObjectName("ContextPrimary")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(self.save_button)

        cancel = QPushButton("Cancel")
        cancel.setObjectName("ContextAction")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

        # Picking the first real account (or Custom, if there are none) is
        # what actually fills the fields in — addItems() alone does not fire
        # currentTextChanged for an index that starts at 0 by construction.
        self._account_changed(self.account.currentText())

    def _account_changed(self, name: str) -> None:
        custom = name == CUSTOM_ACCOUNT
        for field in (self.server, self.port, self.user, self.password, self.client_id):
            field.setReadOnly(not custom)
        self.password_toggle.setEnabled(custom)

        if custom:
            self.server.clear()
            self.port.clear()
            self.user.clear()
            self.password.clear()
            self.client_id.clear()
            self.broker_note.hide()
            self.save_button.setEnabled(True)
            self.server.setFocus()
            return

        account = self._accounts[name]
        if not self._broker_port:
            self.server.clear()
            self.port.clear()
            self.broker_note.setText(
                "Start the broker on Network → Broker to get a reachable "
                "address, or pick Custom to point at one elsewhere."
            )
            self._show_note(blocking=True)
            self.save_button.setEnabled(False)
        elif self._broker_host in LOOPBACK_HOSTS:
            # Only happens with no network at all — the broker binds to the
            # LAN address otherwise. Loopback would point the robot at
            # itself, so refuse rather than hand over an unusable address.
            self.server.clear()
            self.port.clear()
            self.broker_note.setText(
                "This machine has no network address, so the broker is only "
                "reachable from itself. Connect it to the same network as "
                "the robot and restart the broker."
            )
            self._show_note(blocking=True)
            self.save_button.setEnabled(False)
        else:
            self.server.setText(self._broker_host)
            self.port.setText(str(self._broker_port))
            self.save_button.setEnabled(True)
            # Not an error — the port may well be open, and Studio cannot
            # tell without root. But a firewall is the most likely reason a
            # robot provisioned from here still never connects, and this is
            # the moment the user is pointing one at the broker.
            firewall = active_firewall()
            if firewall:
                self.broker_note.setText(
                    f"{firewall} is running. If the robot never connects, "
                    f"open port {self._broker_port} for your network — see "
                    "Network → Broker for the command."
                )
                self._show_note(blocking=False)
            else:
                self.broker_note.hide()
        self.user.setText(account.name)
        self.password.setText(account.password)
        self.client_id.setText(account.name)

    def _show_note(self, *, blocking: bool) -> None:
        """Show broker_note, styled for whether it stops the user or informs."""
        self.broker_note.setObjectName("FieldError" if blocking else "CardBody")
        # Qt only re-evaluates the stylesheet when the object name changes if
        # the widget is repolished; without this the first style sticks.
        self.broker_note.style().unpolish(self.broker_note)
        self.broker_note.style().polish(self.broker_note)
        self.broker_note.show()

    def _set_password_visible(self, visible: bool) -> None:
        self.password.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)
        label = "Hide password" if visible else "Show password"
        self.password_toggle.setToolTip(label)
        self.password_toggle.setAccessibleName(label)

    def save(self) -> None:
        # A Studio-managed broker serves plaintext — it has no certificates,
        # by design. Custom leaves the transport alone: the robot's own
        # default is TLS, which is what a cloud broker needs.
        custom = self.account.currentText() == CUSTOM_ACCOUNT
        error = self._on_save(
            self.server.text(),
            self.port.text(),
            self.user.text(),
            self.password.text(),
            self.client_id.text(),
            None if custom else False,
        )
        if error is None:
            return
        if error:
            self.error.setText(error)
            self.error.show()
            if custom:
                self.password.clear()
                self.password_toggle.setChecked(False)
            return
        self.accept()
