"""Resizable bottom tray for MQTT, app errors, and firmware tooling."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...models.data.app_errors import AppErrors
from ...models.data.mqtt_messages import MqttMessages
from .firmware_flash import FirmwareFlash
from .serial_monitor import SerialMonitor

VISIBLE_MESSAGE_LIMIT = 2000
VISIBLE_ERROR_LIMIT = 500


class DebugTray(QWidget):
    """Views over stored diagnostics; hiding the tray loses nothing."""

    def __init__(
        self,
        messages: MqttMessages,
        errors: AppErrors,
        recorder,
        on_close: Callable[[], None],
        *,
        broker_host: Callable[[], str],
        network=None,
    ) -> None:
        super().__init__()
        self.setObjectName("DebugTray")
        self._messages = messages
        self._errors = errors
        self._recorder = recorder
        self._message_key = None
        self._error_key = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(5)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        title = QLabel("Debug Tray")
        title.setObjectName("DebugTitle")
        header.addWidget(title)
        self.recorder_status = QLabel()
        self.recorder_status.setObjectName("DebugStatus")
        header.addWidget(self.recorder_status)
        header.addStretch(1)
        self.close_button = self._button("×", on_close)
        self.close_button.setObjectName("DebugClose")
        self.close_button.setToolTip("Close Debug Tray")
        self.close_button.setCursor(Qt.PointingHandCursor)
        header.addWidget(self.close_button)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("DebugTabs")
        self.mqtt_tab = self._build_mqtt_tab()
        self.errors_tab = self._build_errors_tab()
        self.serial_tab = SerialMonitor(broker_host=broker_host)
        self.firmware_tab = FirmwareFlash(
            self.serial_tab.disconnect_for_flash,
            serial_monitor=self.serial_tab,
            network=network,
        )
        # Keep Windows interop and USB lifecycle code out of native serial paths.
        from ...services.wsl.windows import is_wsl
        self.wsl_serial = None
        if is_wsl():
            from .wsl_serial import WslSerialIntegration
            self.wsl_serial = WslSerialIntegration(self.serial_tab, self.firmware_tab, self)
        self.tabs.addTab(self.mqtt_tab, "MQTT Activity")
        self.tabs.addTab(self.errors_tab, "App Errors")
        self.tabs.addTab(self.firmware_tab, "Flash Firmware")
        self.tabs.addTab(self.serial_tab, "Serial Monitor")
        layout.addWidget(self.tabs, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh(force=True)

    def _build_mqtt_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 5, 0, 0)
        layout.setSpacing(5)

        toolbar = QHBoxLayout()
        self.message_status = QLabel()
        self.message_status.setObjectName("DebugStatus")
        toolbar.addWidget(self.message_status)
        toolbar.addStretch(1)
        self.show_heartbeats = QCheckBox("Show heartbeats")
        self.show_heartbeats.toggled.connect(lambda: self.refresh(force=True))
        toolbar.addWidget(self.show_heartbeats)
        self.message_wrap = QCheckBox("Wrap")
        self.message_wrap.toggled.connect(
            lambda enabled: self._set_wrap(self.output, enabled)
        )
        toolbar.addWidget(self.message_wrap)
        toolbar.addWidget(self._button("Copy", lambda: self._copy(self.output)))
        toolbar.addWidget(self._button("Clear", self.clear_messages))
        layout.addLayout(toolbar)

        self.output = self._output(
            "MQTT traffic will appear here after Studio's broker starts."
        )
        layout.addWidget(self.output, 1)
        return tab

    def _build_errors_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 5, 0, 0)
        layout.setSpacing(5)

        toolbar = QHBoxLayout()
        self.error_status = QLabel()
        self.error_status.setObjectName("DebugStatus")
        toolbar.addWidget(self.error_status)
        toolbar.addStretch(1)
        self.error_wrap = QCheckBox("Wrap")
        self.error_wrap.toggled.connect(
            lambda enabled: self._set_wrap(self.error_output, enabled)
        )
        toolbar.addWidget(self.error_wrap)
        toolbar.addWidget(self._button("Copy", lambda: self._copy(self.error_output)))
        toolbar.addWidget(self._button("Clear", self.clear_errors))
        layout.addLayout(toolbar)

        self.error_output = self._output(
            "Unhandled application errors will appear here and survive a restart."
        )
        layout.addWidget(self.error_output, 1)
        return tab

    @staticmethod
    def _button(label: str, action: Callable[[], None]) -> QPushButton:
        button = QPushButton(label)
        button.setObjectName("DebugAction")
        button.clicked.connect(action)
        return button

    @staticmethod
    def _output(placeholder: str) -> QPlainTextEdit:
        output = QPlainTextEdit()
        output.setObjectName("DebugLog")
        output.setReadOnly(True)
        output.setLineWrapMode(QPlainTextEdit.NoWrap)
        output.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        output.setPlaceholderText(placeholder)
        return output

    @staticmethod
    def _set_wrap(output: QPlainTextEdit, enabled: bool) -> None:
        output.setLineWrapMode(
            QPlainTextEdit.WidgetWidth if enabled else QPlainTextEdit.NoWrap
        )

    def refresh(self, force: bool = False) -> None:
        if not force and not self.isVisible():
            return
        self.recorder_status.setText(self._recorder.status)
        self._refresh_messages(force)
        self._refresh_errors(force)

    def _refresh_messages(self, force: bool) -> None:
        latest = self._messages.latest_id()
        show_heartbeats = self.show_heartbeats.isChecked()
        key = (latest, show_heartbeats)
        count = self._messages.count()
        self.message_status.setText(f"{count:,} messages stored")
        if not force and key == self._message_key:
            return
        entries = self._messages.recent(
            VISIBLE_MESSAGE_LIMIT, include_heartbeats=show_heartbeats
        )
        self._replace(self.output, "\n".join(message.line for message in entries))
        self._message_key = key

    def _refresh_errors(self, force: bool) -> None:
        latest = self._errors.latest_id()
        count = self._errors.count()
        self.error_status.setText(f"{count:,} errors stored")
        if not force and latest == self._error_key:
            return
        entries = self._errors.recent(VISIBLE_ERROR_LIMIT)
        self._replace(self.error_output, "\n\n".join(error.display for error in entries))
        self._error_key = latest

    @staticmethod
    def _replace(output: QPlainTextEdit, text: str) -> None:
        scroll = output.verticalScrollBar()
        following = scroll.maximum() - scroll.value() <= 2
        output.setPlainText(text)
        if following:
            scroll.setValue(scroll.maximum())

    @staticmethod
    def _copy(output: QPlainTextEdit) -> None:
        text = output.toPlainText()
        if text:
            QApplication.clipboard().setText(text)

    def clear_messages(self) -> None:
        self._messages.clear()
        self.refresh(force=True)

    def clear_errors(self) -> None:
        self._errors.clear()
        self.refresh(force=True)

    def shutdown(self) -> None:
        if self.wsl_serial:
            self.wsl_serial.begin_shutdown()
        self.firmware_tab.shutdown()
        self.serial_tab.shutdown()
        if self.wsl_serial:
            self.wsl_serial.shutdown()
