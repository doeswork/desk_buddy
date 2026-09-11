"""Focused ESP32-S3-CAM build and flash controls for the debug tray."""

from __future__ import annotations

import shlex
import shutil
import time
import uuid
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...services.firmware import (
    BOARD_NAME,
    DEFAULT_UPLOAD_SPEED,
    build_command,
    describe_ports,
    parse_usb_ports,
)
from ...models.config.mqtt_users import users
from ...models.config.robot_profiles import RobotProfile, profiles
from ...models.config.robots import robots
from ...services.network.broker import system as broker_system
from ...services.network.pub_sub.client import mqtt_client
from ...services.network.pub_sub.robot_topics import heartbeat_topic
from ...storage import keys
from ...storage.settings import settings
from .install_firmware_flasher import InstallFirmwareFlasher
from .robot_profile_dialog import RobotProfileDialog


class FirmwareFlash(QWidget):
    """Runs Arduino CLI asynchronously; all expected failures stay in its console."""

    flash_activity = Signal(bool)
    empty_scan = Signal()
    flash_finished = Signal(bool, str)

    def __init__(
        self,
        before_flash: Callable[[str], None] | None = None,
        *,
        serial_monitor=None,
        network=None,
    ) -> None:
        super().__init__()
        self._before_flash = before_flash
        self._serial_monitor = serial_monitor
        self._network = network
        self._flashing = False
        self._port_setup_busy = False
        self.sketch = Path(__file__).resolve().parents[3] / "firmware"
        self._task = ""
        self._captured = bytearray()
        self._pending_profile: RobotProfile | None = None
        self._pending_flash_arguments: list[str] | None = None
        self._account_reload_deadline = 0.0
        self._waiting_for_account_reload = False
        self._account_reload_timer = QTimer(self)
        self._account_reload_timer.setInterval(100)
        self._account_reload_timer.timeout.connect(self._poll_account_reload)
        self._provision_state = ""
        self._provision_deadline = 0.0
        self._next_reconnect_at = 0.0
        self._heartbeat_seen = False
        self._heartbeat_unsubscribe = None
        self._custom_heartbeat_client = None
        self._provision_timer = QTimer(self)
        self._provision_timer.setInterval(250)
        self._provision_timer.timeout.connect(self._poll_provisioning)
        if self._serial_monitor is not None:
            self._serial_monitor.connection_opened.connect(self._serial_opened)
            self._serial_monitor.provisioning_response.connect(self._provision_response)
            self._serial_monitor.heartbeat_received.connect(self._serial_heartbeat)
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._read_output)
        self._process.finished.connect(self._finished)
        self._process.errorOccurred.connect(self._process_error)
        self._installer = InstallFirmwareFlasher(self)
        self._installer.busy_changed.connect(self._setup_running)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 0)
        layout.setSpacing(5)

        identity = QHBoxLayout()
        identity.addWidget(QLabel("Board"))
        self.board_label = QLabel(BOARD_NAME)
        self.board_label.setObjectName("DebugValue")
        identity.addWidget(self.board_label)
        identity.addSpacing(12)
        identity.addWidget(QLabel("Sketch"))
        self.sketch_label = QLabel(str(self.sketch))
        self.sketch_label.setObjectName("DebugValue")
        self.sketch_label.setMinimumWidth(0)
        self.sketch_label.setToolTip(str(self.sketch))
        identity.addWidget(self.sketch_label, 1)
        layout.addLayout(identity)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("USB port"))
        self.port = QComboBox()
        self.port.setObjectName("FlashPort")
        self.port.setEditable(True)
        self.port.setMinimumWidth(180)
        self.port.lineEdit().setPlaceholderText("Connect a board, then scan")
        self.port.currentIndexChanged.connect(self._update_flash_availability)
        self.port.currentTextChanged.connect(self._update_flash_availability)
        controls.addWidget(self.port, 1)
        self.scan_button = self._button("List USB Devices", self.scan)
        controls.addWidget(self.scan_button)
        self.port_requirement = QLabel("Required: select a USB port before flashing")
        self.port_requirement.setObjectName("FieldError")
        controls.addWidget(self.port_requirement)
        controls.addSpacing(8)
        controls.addWidget(QLabel("Speed"))
        self.speed = QComboBox()
        self.speed.addItems(["921600", "460800", "230400", "115200"])
        self.speed.setCurrentText(DEFAULT_UPLOAD_SPEED)
        controls.addWidget(self.speed)
        self.erase_first = QCheckBox("Erase first")
        self.erase_first.setToolTip(
            "Erase all flash before uploading. This also deletes saved ESP32 "
            "Wi-Fi and MQTT credentials; the guided profile will be restored after upload."
        )
        controls.addWidget(self.erase_first)
        self.verbose = QCheckBox("Verbose")
        controls.addWidget(self.verbose)
        layout.addLayout(controls)

        actions = QHBoxLayout()
        self.status = QLabel("Ready")
        self.status.setObjectName("DebugStatus")
        actions.addWidget(self.status)
        actions.addStretch(1)
        self.build_button = self._button("Verify Build", self.compile)
        actions.addWidget(self.build_button)
        self.flash_button = self._button("Flash Firmware", self.flash)
        self.flash_button.setObjectName("DebugPrimaryAction")
        actions.addWidget(self.flash_button)
        self.retry_profile_button = self._button(
            "Retry Robot Setup", self.retry_provisioning
        )
        self.retry_profile_button.setToolTip(
            "Send the saved Wi-Fi and MQTT profile over serial without flashing again"
        )
        actions.addWidget(self.retry_profile_button)
        self.stop_button = self._button("Stop", self.stop)
        self.stop_button.setEnabled(False)
        actions.addWidget(self.stop_button)
        self.wrap_lines = QCheckBox("Wrap")
        self.wrap_lines.toggled.connect(self._set_wrap)
        actions.addWidget(self.wrap_lines)
        actions.addWidget(self._button("Copy", self.copy_output))
        actions.addWidget(self._button("Clear", self.clear_output))
        layout.addLayout(actions)

        self.output = QPlainTextEdit()
        self.output.setObjectName("DebugLog")
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.output.setPlaceholderText(
            "USB discovery, compiler output, upload progress, and firmware-tool errors appear here."
        )
        self.output.document().setMaximumBlockCount(20_000)
        layout.addWidget(self.output, 1)
        self._update_flash_availability()

    def _set_wrap(self, enabled: bool) -> None:
        self.output.setLineWrapMode(
            QPlainTextEdit.WidgetWidth if enabled else QPlainTextEdit.NoWrap
        )

    def _has_selected_port(self) -> bool:
        index = self.port.currentIndex()
        return (
            index >= 0
            and self.port.currentText() == self.port.itemText(index)
            and bool(self.port.itemData(index))
        )

    def _update_flash_availability(self, *_unused) -> None:
        selected = self._has_selected_port()
        self.port_requirement.setVisible(not selected)
        self.flash_button.setEnabled(
            selected and not self._port_setup_busy and not self._task
            and not self._waiting_for_account_reload
            and not self._provision_state
            and self._process.state() == QProcess.NotRunning
        )
        self.retry_profile_button.setEnabled(
            selected and not self._port_setup_busy and not self._task
            and not self._waiting_for_account_reload
            and not self._provision_state
            and self._process.state() == QProcess.NotRunning
        )

    @staticmethod
    def _button(label: str, action) -> QPushButton:
        button = QPushButton(label)
        button.setObjectName("DebugAction")
        button.clicked.connect(action)
        return button

    @property
    def selected_port(self) -> str:
        text = self.port.currentText().strip()
        index = self.port.currentIndex()
        if index >= 0 and text == self.port.itemText(index):
            return str(self.port.itemData(index) or text)
        return text.split(" — ", 1)[0]

    def scan(self) -> None:
        self._installer.ensure(lambda: self._start("scan", ["board", "list", "--format", "json"]))

    def compile(self) -> None:
        self._installer.ensure(self._compile_ready)

    def _compile_ready(self) -> None:
        self._start(
            "build",
            build_command(
                self.sketch,
                upload_speed=self.speed.currentText(),
                verbose=self.verbose.isChecked(),
            ),
        )

    def flash(self) -> None:
        if not self._port_setup_busy and self.selected_port:
            self._installer.ensure(self._flash_ready)

    def retry_provisioning(self) -> None:
        """Resend the selected saved profile without compiling or uploading."""
        if (
            self._port_setup_busy
            or self._task
            or self._provision_state
            or self._process.state() != QProcess.NotRunning
        ):
            return
        profile = profiles().find(settings().get(keys.ROBOT_SELECTED_PROFILE))
        if profile is None:
            self._append("ERROR: No saved robot profile is available to retry.")
            self.status.setText("No saved robot profile")
            return
        if self._serial_monitor is None:
            self._append("ERROR: The serial monitor is unavailable.")
            self.status.setText("Serial monitor unavailable")
            return
        if not self.selected_port:
            self._append("ERROR: Select the robot's USB port before retrying setup.")
            self.status.setText("No USB port selected")
            return
        self._pending_profile = profile
        self._begin_provisioning(after_flash=False)

    def _flash_ready(self) -> None:
        if self._port_setup_busy:
            return
        port = self.selected_port
        if not port:
            self._append("ERROR: Select a USB port before flashing. Try List USB Devices.")
            self.status.setText("No USB port selected")
            return
        default_server = ""
        default_port = 1883
        if self._network is not None:
            default_server = self._network.broker_host()
            default_port = self._network.system_broker().port or default_port
        existing = profiles().all()
        selected = profiles().find(settings().get(keys.ROBOT_SELECTED_PROFILE))
        accounts = self._network.accounts() if self._network is not None else broker_system.accounts()
        dialog = RobotProfileDialog(
            self,
            profile=selected,
            profiles=existing,
            accounts=accounts,
            default_server=default_server,
            default_port=default_port,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._append("Profile selection cancelled.")
            return
        profile = dialog.profile()
        profiles().save(profile)
        settings().set(keys.ROBOT_SELECTED_PROFILE, profile.profile_id)
        settings().sync()

        erase_note = (
            " This clears ESP32 NVS, including its saved Wi-Fi and MQTT "
            "credentials; Studio will restore this profile after upload."
            if self.erase_first.isChecked() else ""
        )
        answer = QMessageBox.question(
            self,
            "Flash firmware?",
            f"Compile and upload Desk Buddy firmware to {port}?{erase_note}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            self._append("Flash cancelled.")
            return
        self._pending_profile = profile
        self._pending_flash_arguments = build_command(
            self.sketch,
            port=port,
            upload_speed=self.speed.currentText(),
            erase=self.erase_first.isChecked(),
            verbose=self.verbose.isChecked(),
        )
        if profile.broker_kind == "local" and not self._prepare_local_account(profile):
            return
        self._start_pending_flash()

    def _prepare_local_account(self, profile: RobotProfile) -> bool:
        """Prepare an account now, or asynchronously continue after reload."""
        if not profile.mqtt_user or not profile.mqtt_password:
            self._fail_account_preparation(
                "A local robot account needs a username and password."
            )
            return False
        if self._network is None:
            users().record(profile.mqtt_user, profile.mqtt_password)
            return True
        change = self._network.create_account(
            profile.mqtt_user,
            profile.mqtt_password,
            f"{profile.mqtt_user}/#",
        )
        if not change.changed:
            self._fail_account_preparation(
                change.problem or "Mosquitto did not accept the account change."
            )
            return False
        # Keep the password available for automatic registration even when the
        # broker account existed before Studio saw it.
        users().record(profile.mqtt_user, profile.mqtt_password)
        if not change.applied and self._network.account_reload_pending():
            self._append("MQTT account saved; waiting for broker reload…")
            self.status.setText("Applying MQTT account…")
            self._account_reload_deadline = time.monotonic() + 30
            self._set_account_reload_waiting(True)
            self._account_reload_timer.start()
            return False
        return True

    def _poll_account_reload(self) -> None:
        """Resume flashing after Network's background reload has completed."""
        if not self._waiting_for_account_reload or self._network is None:
            self._account_reload_timer.stop()
            return
        if not self._network.account_reload_pending():
            self._account_reload_timer.stop()
            self._set_account_reload_waiting(False)
            self._append("✓ MQTT account is active.")
            self._start_pending_flash()
            return
        if not self._network.account_reload.running and self._network.account_problem:
            self._fail_account_preparation(self._network.account_problem)
            return
        if time.monotonic() >= self._account_reload_deadline:
            self._fail_account_preparation(
                "The broker account was saved but Mosquitto has not reloaded it yet."
            )

    def _start_pending_flash(self) -> None:
        arguments = self._pending_flash_arguments
        self._pending_flash_arguments = None
        if arguments is not None:
            self._start("flash", arguments)

    def _fail_account_preparation(self, problem: str) -> None:
        self._account_reload_timer.stop()
        self._set_account_reload_waiting(False)
        self._append(f"ERROR: Could not prepare MQTT account: {problem}")
        self.status.setText("MQTT account setup failed")
        profile = self._pending_profile
        if profile is not None:
            profiles().update(
                profile.profile_id,
                last_result=f"MQTT account setup failed: {problem}",
                last_provisioned_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
        self._pending_profile = None
        self._pending_flash_arguments = None

    def _set_account_reload_waiting(self, waiting: bool) -> None:
        self._waiting_for_account_reload = waiting
        for control in (
            self.scan_button, self.build_button,
            self.port, self.speed, self.erase_first, self.verbose,
            self.retry_profile_button,
        ):
            control.setEnabled(not waiting)
        self.stop_button.setEnabled(waiting)
        self._update_flash_availability()

    def _start(self, task: str, arguments: list[str]) -> None:
        if task == "flash" and self._port_setup_busy:
            return
        if self._process.state() != QProcess.NotRunning:
            self._append("A firmware command is already running.")
            return
        program = self._installer.program or shutil.which("arduino-cli")
        if not program:
            self._append(
                "ERROR: arduino-cli was not found. Install Arduino CLI and the Espressif ESP32 core, then try again."
            )
            self.status.setText("Arduino CLI missing")
            if task == "flash":
                self._save_provision_result("Upload could not start")
                self._pending_profile = None
            return
        if not self.sketch.is_dir():
            self._append(f"ERROR: Firmware sketch folder does not exist: {self.sketch}")
            self.status.setText("Firmware folder missing")
            if task == "flash":
                self._save_provision_result("Firmware folder missing")
                self._pending_profile = None
            return

        self._task = task
        if task == "flash":
            self._flashing = True
            self.flash_activity.emit(True)
            if self._before_flash is not None:
                self._before_flash(self.selected_port)
        self._captured.clear()
        self._append(f"\n$ arduino-cli {shlex.join(arguments)}")
        self.status.setText({"scan": "Scanning USB…", "build": "Compiling…", "flash": "Flashing…"}[task])
        self._set_running(True)
        self._process.setWorkingDirectory(str(self.sketch.parent))
        self._process.start(program, arguments)

    def _read_output(self) -> None:
        chunk = bytes(self._process.readAllStandardOutput())
        if self._task == "scan":
            self._captured.extend(chunk)
        else:
            self._append(chunk.decode(errors="replace").rstrip("\n"))

    def _finished(self, exit_code: int, _status) -> None:
        task = self._task
        if task == "scan" and exit_code == 0:
            ports = parse_usb_ports(bytes(self._captured))
            previous = self.selected_port
            self.port.clear()
            for port in ports:
                self.port.addItem(port.display, port.address)
            if previous:
                match = self.port.findData(previous)
                if match >= 0:
                    self.port.setCurrentIndex(match)
                elif not ports:
                    self.port.setEditText(previous)
            self._append(describe_ports(ports))
            if not ports:
                self.empty_scan.emit()
        elif task == "scan" and self._captured:
            self._append(bytes(self._captured).decode(errors="replace").rstrip())
        if exit_code == 0:
            labels = {"scan": "USB scan complete", "build": "Build verified", "flash": "Firmware flashed"}
            self.status.setText(labels.get(task, "Complete"))
            if task != "scan":
                self._append("✓ Command completed successfully.")
            if task == "flash":
                self.flash_finished.emit(True, self.selected_port)
        else:
            self.status.setText(f"{task.title() or 'Command'} failed")
            self._append(f"ERROR: arduino-cli exited with status {exit_code}.")
            if task == "flash":
                self._save_provision_result("Upload failed")
                self.flash_finished.emit(False, self.selected_port)
                self._pending_profile = None
        self._task = ""
        self._set_running(False)
        if task == "flash" and exit_code == 0:
            self._begin_provisioning()

    def _process_error(self, error) -> None:
        self._append(f"ERROR: {self._process.errorString()}")
        if error == QProcess.FailedToStart:
            if self._task == "flash":
                self._save_provision_result("Upload could not start")
                self.flash_finished.emit(False, self.selected_port)
                self._pending_profile = None
            self.status.setText("Could not start Arduino CLI")
            self._task = ""
            self._set_running(False)

    def _set_running(self, running: bool) -> None:
        if not running and self._flashing:
            self._flashing = False
            self.flash_activity.emit(False)
        for control in (
            self.scan_button, self.build_button,
            self.port, self.speed, self.erase_first, self.verbose,
            self.retry_profile_button,
        ):
            control.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self._update_flash_availability()

    def set_port_setup_busy(self, busy: bool) -> None:
        self._port_setup_busy = busy
        self._update_flash_availability()

    def _setup_running(self, running: bool) -> None:
        self._task = "setup" if running else ""
        self._set_running(running)

    def stop(self) -> None:
        if self._waiting_for_account_reload:
            self._account_reload_timer.stop()
            self._set_account_reload_waiting(False)
            self._save_provision_result("MQTT account setup cancelled")
            self._pending_profile = None
            self._pending_flash_arguments = None
            self.status.setText("Flash cancelled")
            self._append("Flash cancelled while waiting for the MQTT account reload.")
            return
        if self._installer.busy:
            self._installer.stop()
            return
        if self._process.state() == QProcess.NotRunning:
            return
        self._append("Stopping firmware command…")
        self._process.kill()

    def shutdown(self) -> None:
        self._account_reload_timer.stop()
        self._waiting_for_account_reload = False
        self._pending_flash_arguments = None
        self._cancel_provisioning()
        self._installer.shutdown()
        if self._process.state() != QProcess.NotRunning:
            self._process.kill()
            self._process.waitForFinished(1000)

    def copy_output(self) -> None:
        text = self.output.toPlainText()
        if text:
            QApplication.clipboard().setText(text)

    def clear_output(self) -> None:
        self.output.clear()

    def _append(self, text: str) -> None:
        if not text:
            return
        self.output.appendPlainText(text)
        scroll = self.output.verticalScrollBar()
        scroll.setValue(scroll.maximum())

    # ---- post-upload provisioning --------------------------------------
    def _begin_provisioning(self, *, after_flash: bool = True) -> None:
        profile = self._pending_profile
        monitor = self._serial_monitor
        if profile is None or monitor is None:
            if profile is not None:
                self._save_provision_result("Firmware flashed; serial monitor unavailable")
                self._pending_profile = None
            return
        self._provision_state = "reconnect"
        self._provision_deadline = time.monotonic() + 12
        self._next_reconnect_at = 0.0
        self.status.setText(
            "Firmware flashed; reconnecting to robot…"
            if after_flash else "Connecting to robot for profile retry…"
        )
        self._append(
            "Reconnecting to the same USB serial port for provisioning…"
            if after_flash else "Retrying the saved profile without flashing firmware…"
        )
        self._update_flash_availability()
        if not monitor.serial.isOpen():
            monitor.port.setEditText(self.selected_port)
            monitor.connect()
        if monitor.serial.isOpen():
            self._serial_opened(self.selected_port)
        self._provision_timer.start()

    def _serial_opened(self, _port: str) -> None:
        if self._provision_state == "reconnect":
            # Opening a USB serial port succeeds before the ESP32 has reached
            # setup()/loop(). Sending immediately races its boot and silently
            # drops the command. A firmware heartbeat is the readiness signal.
            self._provision_state = "serial-ready"
            self._provision_deadline = time.monotonic() + 12
            self.status.setText("Serial connected; waiting for the robot…")

    def _serial_heartbeat(self, _heartbeat: dict) -> None:
        if self._provision_state == "serial-ready":
            self._send_profile()

    def _send_profile(self) -> None:
        if self._provision_state != "serial-ready" or self._serial_monitor is None:
            return
        error = self._serial_monitor.send_profile(self._pending_profile)
        if error:
            self._fail_provisioning(error)
            return
        self._provision_state = "ack"
        self._provision_deadline = time.monotonic() + 8

    def _provision_response(self, response: dict) -> None:
        if self._provision_state != "ack":
            return
        kind = response.get("serial_provisioning")
        if kind not in {"profile", "error"}:
            return
        if response.get("status") == "saved":
            self._wait_for_heartbeat()
        elif response.get("status") == "error":
            self._fail_provisioning(str(response.get("error") or "The robot rejected the profile."))

    def _wait_for_heartbeat(self) -> None:
        profile = self._pending_profile
        if profile is None:
            return
        self._heartbeat_seen = False
        if profile.broker_kind == "custom":
            self._start_custom_heartbeat(profile)
        else:
            client = mqtt_client()
            client.reconcile()
            self._heartbeat_unsubscribe = client.subscribe(
                heartbeat_topic(profile.mqtt_user), self._heartbeat_callback
            )
        self._provision_state = "heartbeat"
        self._provision_deadline = time.monotonic() + 20
        self.status.setText("Robot profile saved; waiting for MQTT heartbeat…")
        self._append("Waiting for the robot's MQTT heartbeat…")

    def _heartbeat_callback(self, _topic: str, payload: dict) -> None:
        # MqttClient invokes callbacks on paho's thread. This flag is the only
        # cross-thread state touched; the GUI consumes it from its timer.
        self._heartbeat_seen = (
            payload.get("sender") == "firmware"
            and payload.get("log") == "heartbeat"
        )

    def _start_custom_heartbeat(self, profile: RobotProfile) -> None:
        """Verify a custom endpoint without replacing Studio's shared client."""
        try:
            import paho.mqtt.client as mqtt

            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"desk-buddy-verifier-{uuid.uuid4().hex[:12]}",
                protocol=mqtt.MQTTv311,
            )
            client.username_pw_set(profile.mqtt_user, profile.mqtt_password)
            if profile.tls:
                client.tls_set()
            topic = heartbeat_topic(profile.mqtt_user)

            def connected(_client, _userdata, _flags, reason_code, _properties):
                if getattr(reason_code, "value", reason_code) == 0:
                    _client.subscribe(topic, qos=1)

            def message(_client, _userdata, message):
                if message.topic != topic:
                    return
                try:
                    payload = json.loads(bytes(message.payload))
                except (TypeError, ValueError):
                    return
                if isinstance(payload, dict):
                    self._heartbeat_callback(topic, payload)

            client.on_connect = connected
            client.on_message = message
            client.connect_async(profile.broker_server, profile.broker_port, keepalive=30)
            client.loop_start()
            self._custom_heartbeat_client = client
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            self._append(f"Custom MQTT heartbeat verifier could not start: {error}")

    def _poll_provisioning(self) -> None:
        if self._provision_state == "heartbeat" and self._heartbeat_seen:
            self._complete_provisioning("Robot connected — MQTT heartbeat received.")
            return
        if self._provision_state == "reconnect" and self._serial_monitor is not None:
            if self._serial_monitor.serial.isOpen():
                self._send_profile()
                return
            if time.monotonic() >= self._next_reconnect_at:
                self._serial_monitor.connect()
                self._next_reconnect_at = time.monotonic() + 1
                return
        if self._provision_state and time.monotonic() > self._provision_deadline:
            if self._provision_state == "reconnect":
                self._fail_provisioning(
                    "Could not reconnect to the same USB serial port after upload."
                )
            elif self._provision_state == "serial-ready":
                self._fail_provisioning(
                    "Serial reconnected, but the robot did not become ready for provisioning."
                )
            elif self._provision_state == "ack":
                self._fail_provisioning(
                    "The robot did not acknowledge serial provisioning. Check the USB cable and firmware."
                )
            else:
                self._fail_provisioning(self._heartbeat_timeout_detail())

    def _heartbeat_timeout_detail(self) -> str:
        profile = self._pending_profile
        if profile is not None and profile.broker_kind == "custom":
            likely = "broker reachability, TLS mode, credentials, or ACL access"
        else:
            likely = "Wi-Fi or broker reachability; then check TLS mode, credentials, or ACL access"
        return f"MQTT heartbeat timed out. Profile saved. Likely problem: {likely}."

    def _complete_provisioning(self, message: str, *, register: bool = True) -> None:
        profile = self._pending_profile
        if profile is not None and register:
            users().record(profile.mqtt_user, profile.mqtt_password)
            robot, problem = robots().upsert(profile.mqtt_user, profile.name)
            if problem:
                self._append(f"WARNING: Robot heartbeat received, but registration failed: {problem}")
            self._save_provision_result("Connected")
        elif profile is not None:
            self._save_provision_result("Profile saved")
        self._append(f"✓ {message}")
        self.status.setText(message)
        self._cancel_provisioning(clear_profile=True)

    def _fail_provisioning(self, detail: str) -> None:
        self._append(f"ERROR: {detail}")
        self.status.setText("Robot setup needs attention")
        self._save_provision_result(detail)
        self._cancel_provisioning(clear_profile=True)

    def _save_provision_result(self, result: str) -> None:
        profile = self._pending_profile
        if profile is None:
            return
        self._save_profile_result(profile, result)

    @staticmethod
    def _save_profile_result(profile: RobotProfile, result: str) -> None:
        profiles().update(
            profile.profile_id,
            last_result=result,
            last_provisioned_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def _cancel_provisioning(self, *, clear_profile: bool = False) -> None:
        self._provision_timer.stop()
        if self._heartbeat_unsubscribe is not None:
            self._heartbeat_unsubscribe()
            self._heartbeat_unsubscribe = None
        client = self._custom_heartbeat_client
        self._custom_heartbeat_client = None
        if client is not None:
            try:
                client.disconnect()
                client.loop_stop()
            except (OSError, RuntimeError):
                pass
        self._provision_state = ""
        if clear_profile:
            self._pending_profile = None
        self._update_flash_availability()
