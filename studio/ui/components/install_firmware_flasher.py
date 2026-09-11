"""On-demand firmware tool setup UI, separate from serial and upload controls."""
from __future__ import annotations

import queue
import threading

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtWidgets import QMessageBox

from ...services.firmware import install_firmware_flasher as tools


class InstallFirmwareFlasher(QObject):
    busy_changed = Signal(bool)

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.busy = False
        self.program = ""
        self._callback = None
        self._phase = ""
        self._installing = False
        self._output = bytearray()
        self._cancel = threading.Event()
        self._events = queue.Queue()
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._read)
        self._process.finished.connect(self._finished)
        self._process.errorOccurred.connect(self._error)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(lambda: self.stop("Firmware tool setup timed out. Try again."))
        self._poller = QTimer(self)
        self._poller.setInterval(100)
        self._poller.timeout.connect(self._poll)

    def ensure(self, callback):
        if self.busy or self.view._task:
            return
        self._callback = callback
        self._installing = False
        self.busy = True
        self.busy_changed.emit(True)
        try:
            self.program = tools.find_cli()
        except OSError as error:
            self.stop(f"Cannot access firmware tools: {error}")
            return
        if self.program:
            self._run("check", ["core", "list", "--format", "json"])
        else:
            self._offer("Arduino CLI and the Espressif ESP32 core are missing.")

    def _offer(self, reason):
        answer = QMessageBox.question(
            self.view, "Install firmware tools?",
            reason + "\n\nInstall the tools needed to list USB devices, build, and flash firmware? "
            "This is a large download and may take several minutes. "
            "Tools are installed for your user; no Studio restart is needed.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            self.stop("Firmware tool setup cancelled.")
            return
        self._installing = True
        if self.program:
            self._index()
            return
        try:
            destination = tools.managed_cli()
        except OSError as error:
            self.stop(f"Cannot create the firmware tools folder: {error}")
            return
        self._cancel.clear()
        self._phase = "download"
        self.view.status.setText("Downloading Arduino CLI…")
        def download():
            try:
                path = tools.install_cli(destination, self._cancel, lambda s: self._events.put(("log", s)))
                self._events.put(("downloaded", path))
            except Exception as error:
                self._events.put(("failed", str(error)))
        threading.Thread(target=download, name="firmware-tools-download", daemon=True).start()
        self._poller.start()

    def _poll(self):
        while not self._events.empty():
            kind, value = self._events.get_nowait()
            if kind == "log":
                self.view._append(value)
            else:
                self._poller.stop()
                self._phase = ""  # The worker has finished, including failures.
                if self._cancel.is_set():
                    self._finish(False)
                elif kind == "failed":
                    self.stop("Firmware tool setup failed: " + value)
                else:
                    self.program = value
                    self._index()

    def _index(self):
        self._run("index", ["core", "update-index", "--additional-urls", tools.ESP32_INDEX])

    def _run(self, phase, arguments):
        self._phase = phase
        self._output.clear()
        label = {"check": "Checking firmware tools…", "verify": "Verifying firmware tools…",
                 "index": "Downloading board indexes…", "core": "Installing the ESP32 compiler and tools…"}[phase]
        self.view.status.setText(label)
        self.view._append(label)
        self._timeout.start(20 * 60 * 1000 if phase in {"index", "core"} else 30_000)
        self._process.start(self.program, arguments)

    def _read(self):
        chunk = bytes(self._process.readAllStandardOutput())
        self._output.extend(chunk)
        if self._phase in {"index", "core"}:
            self._output = self._output[-128 * 1024:]
            self.view._append(chunk.decode(errors="replace").rstrip())

    def _finished(self, code, _status):
        self._timeout.stop()
        if self._phase == "stopping":
            self._finish(False)
            return
        if not self._phase:
            return
        self._read()
        if code:
            self.view._append(self._output.decode(errors="replace"))
            if self._phase == "check":
                self._offer("Arduino CLI could not load its board packages. Setup can download them again.")
            else:
                self.stop("Firmware tool setup failed. See the output above and try again.")
            return
        if self._phase == "index":
            self._run("core", ["core", "install", "esp32:esp32", "--additional-urls", tools.ESP32_INDEX])
        elif self._phase == "core":
            self._run("verify", ["core", "list", "--format", "json"])
        else:
            try:
                ready = tools.has_esp32(bytes(self._output))
            except (ValueError, TypeError, AttributeError) as error:
                self.stop(f"Could not read the installed firmware tools: {error}")
                return
            if ready:
                self.view.status.setText("Firmware tools ready")
                self._finish(True)
            elif self._installing:
                self.stop("The ESP32 core is still missing after installation. Try setup again.")
            else:
                self._offer("The Espressif ESP32 core is missing.")

    def _error(self, error):
        if error == QProcess.FailedToStart:
            self.stop("Cannot start Arduino CLI: " + self._process.errorString())

    def _finish(self, success):
        callback, self._callback = self._callback, None
        self._phase = ""
        self.busy = False
        self.busy_changed.emit(False)
        if success and callback:
            callback()

    def stop(self, message="Firmware tool setup cancelled."):
        if not self.busy:
            return
        downloading = self._phase in {"download", "cancelling_download"}
        running = self._process.state() != QProcess.NotRunning
        self._phase = "cancelling_download" if downloading else "stopping" if running else ""
        self._callback = None
        self._timeout.stop()
        self._cancel.set()
        self._process.kill()
        self.view._append(message)
        self.view.status.setText(message.splitlines()[0])
        # Keep setup reserved until the download worker acknowledges cancellation.
        if not downloading and not running:
            self._finish(False)

    def shutdown(self):
        self.stop()
        self._poller.stop()
        self._process.waitForFinished(1000)
