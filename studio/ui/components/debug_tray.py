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
from ...services.debug_tray import page_text
from .firmware_flash import FirmwareFlash
from .manual_control import ManualControl
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
        current_page: Callable[[], QWidget | None],
        page_header: Callable[[], list[str]] | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("DebugTray")
        self._messages = messages
        self._errors = errors
        self._recorder = recorder
        self._current_page = current_page
        self._page_header = page_header
        self._message_key = None
        self._error_key = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(5)

        # No header row. A titled strip saying "Debug Tray" above a tray the
        # user just opened is a line of chrome that names what they are
        # already looking at, and in a dock this short every row it takes is
        # a row the contents do not get. The two things that row carried
        # which are not decoration — the recorder's state, and the way out —
        # ride in the tab bar's corner instead, on a row that already exists.
        self.tabs = QTabWidget()
        self.tabs.setObjectName("DebugTabs")

        # Only the close button rides in the corner. The recorder's line is
        # long — a host and a port — and a corner widget is laid out over
        # the tab bar rather than beside it, so putting it here covered the
        # tabs. It belongs with the traffic it describes anyway, and sits in
        # the MQTT tab's own toolbar.
        self.close_button = self._button("×", on_close)
        self.close_button.setObjectName("DebugClose")
        self.close_button.setToolTip("Close Debug Tray")
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.tabs.setCornerWidget(self.close_button, Qt.TopRightCorner)
        self.mqtt_tab = self._build_mqtt_tab()
        self.errors_tab = self._build_errors_tab()
        self.page_tab = self._build_page_tab()
        self.manual_tab = ManualControl()
        self.serial_tab = SerialMonitor(broker_host=broker_host)
        self.firmware_tab = FirmwareFlash(self.serial_tab.disconnect_for_flash)
        self.tabs.addTab(self.mqtt_tab, "MQTT Activity")
        self.tabs.addTab(self.errors_tab, "App Errors")
        self.tabs.addTab(self.page_tab, "Page Text")
        # Driving the arm sits next to the MQTT log on purpose: the command
        # you just sent and the traffic it produced are one tab apart.
        self.tabs.addTab(self.manual_tab, "Manual Control")
        self.tabs.addTab(self.firmware_tab, "Flash Firmware")
        self.tabs.addTab(self.serial_tab, "Serial Monitor")
        # Arriving on Page Text is the ask for a fresh reading; nothing else
        # in the tray needs to know a tab changed.
        self.tabs.currentChanged.connect(
            lambda index: self.capture_page()
            if self.tabs.widget(index) is self.page_tab else None
        )
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
        self.recorder_status = QLabel()
        self.recorder_status.setObjectName("DebugStatus")
        toolbar.addWidget(self.recorder_status)
        separator = QLabel("·")
        separator.setObjectName("DebugStatus")
        toolbar.addWidget(separator)
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

    def _build_page_tab(self) -> QWidget:
        """The page on screen, as text you can paste somewhere.

        Deliberately not on the refresh timer the other tabs share. The two
        log tabs tail a stream that grows on its own; this one is a snapshot
        of a page that only changes when the user changes it, and walking
        the widget tree twice a second to find out it did not would be work
        for nothing. It captures on arrival and on the button — and a
        snapshot that holds still is what makes it selectable, since text
        replaced under a drag loses the selection.
        """
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 5, 0, 0)
        layout.setSpacing(5)

        toolbar = QHBoxLayout()
        self.page_status = QLabel()
        self.page_status.setObjectName("DebugStatus")
        toolbar.addWidget(self.page_status)
        toolbar.addStretch(1)
        self.page_wrap = QCheckBox("Wrap")
        self.page_wrap.toggled.connect(
            lambda enabled: self._set_wrap(self.page_output, enabled)
        )
        toolbar.addWidget(self.page_wrap)
        toolbar.addWidget(self._button("Capture", self.capture_page))
        toolbar.addWidget(self._button("Copy", lambda: self._copy(self.page_output)))
        layout.addLayout(toolbar)

        self.page_output = self._output(
            "Everything visible on the page behind this tray, as text."
        )
        layout.addWidget(self.page_output, 1)
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
        # Robots are marked on Network → Robots, so the target picker can go
        # stale while this tab sits open. Asking is a snapshot compare.
        self.manual_tab.refresh()
        # `force` is the tray being opened or a control being toggled — a
        # user action, and the one moment a stale capture would mislead.
        # The timer's own ticks leave the snapshot alone.
        if force and self.tabs.currentWidget() is self.page_tab:
            self.capture_page()

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

    def capture_page(self) -> None:
        """Re-read the page behind the tray.

        The tray itself is excluded by construction: `current_page` hands
        over the workspace's page widget, not the window, so the output
        never describes the panel it is being read in. `page_header` names
        where that page is, which the widgets themselves cannot say.
        """
        header = self._page_header() if self._page_header is not None else None
        self._replace(self.page_output, page_text(self._current_page(), header))
        lines = self.page_output.document().blockCount()
        self.page_status.setText(f"{lines:,} lines captured")

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
        self.firmware_tab.shutdown()
        self.serial_tab.shutdown()
