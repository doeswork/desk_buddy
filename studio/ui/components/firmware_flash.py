"""Focused ESP32-S3-CAM build and flash controls for the debug tray."""

from __future__ import annotations

import shlex
import shutil
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
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


class FirmwareFlash(QWidget):
    """Runs Arduino CLI asynchronously; all expected failures stay in its console."""

    def __init__(self, before_flash: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self._before_flash = before_flash
        self.sketch = Path(__file__).resolve().parents[3] / "firmware"
        self._task = ""
        self._captured = bytearray()
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._read_output)
        self._process.finished.connect(self._finished)
        self._process.errorOccurred.connect(self._process_error)

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
        controls.addWidget(self.port, 1)
        self.scan_button = self._button("List USB Devices", self.scan)
        controls.addWidget(self.scan_button)
        controls.addSpacing(8)
        controls.addWidget(QLabel("Speed"))
        self.speed = QComboBox()
        self.speed.addItems(["921600", "460800", "230400", "115200"])
        self.speed.setCurrentText(DEFAULT_UPLOAD_SPEED)
        controls.addWidget(self.speed)
        self.erase_first = QCheckBox("Erase first")
        self.erase_first.setToolTip("Erase all flash before uploading the new firmware")
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

    def _set_wrap(self, enabled: bool) -> None:
        self.output.setLineWrapMode(
            QPlainTextEdit.WidgetWidth if enabled else QPlainTextEdit.NoWrap
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
        self._start("scan", ["board", "list", "--format", "json"])

    def compile(self) -> None:
        self._start(
            "build",
            build_command(
                self.sketch,
                upload_speed=self.speed.currentText(),
                verbose=self.verbose.isChecked(),
            ),
        )

    def flash(self) -> None:
        port = self.selected_port
        if not port:
            self._append("ERROR: Select a USB port before flashing. Try List USB Devices.")
            self.status.setText("No USB port selected")
            return
        erase_note = " All existing flash data will be erased first." if self.erase_first.isChecked() else ""
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
        if self._before_flash is not None:
            self._before_flash(port)
        self._start(
            "flash",
            build_command(
                self.sketch,
                port=port,
                upload_speed=self.speed.currentText(),
                erase=self.erase_first.isChecked(),
                verbose=self.verbose.isChecked(),
            ),
        )

    def _start(self, task: str, arguments: list[str]) -> None:
        if self._process.state() != QProcess.NotRunning:
            self._append("A firmware command is already running.")
            return
        program = shutil.which("arduino-cli")
        if not program:
            self._append(
                "ERROR: arduino-cli was not found. Install Arduino CLI and the Espressif ESP32 core, then try again."
            )
            self.status.setText("Arduino CLI missing")
            return
        if not self.sketch.is_dir():
            self._append(f"ERROR: Firmware sketch folder does not exist: {self.sketch}")
            self.status.setText("Firmware folder missing")
            return

        self._task = task
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
        elif task == "scan" and self._captured:
            self._append(bytes(self._captured).decode(errors="replace").rstrip())
        if exit_code == 0:
            labels = {"scan": "USB scan complete", "build": "Build verified", "flash": "Firmware flashed"}
            self.status.setText(labels.get(task, "Complete"))
            if task != "scan":
                self._append("✓ Command completed successfully.")
        else:
            self.status.setText(f"{task.title() or 'Command'} failed")
            self._append(f"ERROR: arduino-cli exited with status {exit_code}.")
        self._task = ""
        self._set_running(False)

    def _process_error(self, error) -> None:
        self._append(f"ERROR: {self._process.errorString()}")
        if error == QProcess.FailedToStart:
            self.status.setText("Could not start Arduino CLI")
            self._task = ""
            self._set_running(False)

    def _set_running(self, running: bool) -> None:
        for control in (
            self.scan_button, self.build_button, self.flash_button,
            self.port, self.speed, self.erase_first, self.verbose,
        ):
            control.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def stop(self) -> None:
        if self._process.state() == QProcess.NotRunning:
            return
        self._append("Stopping firmware command…")
        self._process.kill()

    def shutdown(self) -> None:
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
