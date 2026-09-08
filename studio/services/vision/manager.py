"""Qt-owned installation, process, MQTT status, and preview controller."""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Qt, Signal

from ...storage import keys
from ...storage.settings import Settings, settings
from ..network import mqtt_client
from ..network.broker.finder import studio_endpoint
from .access import PASSWORD_ENV, USERNAME_ENV, VisionCredentials, ensure_access
from .bootstrap import (
    PYTHON_VERSION,
    RUNTIME_REQUIREMENTS,
    materialize_bootstrap,
    runtime_fingerprint,
    vision_root,
)
from .catalog import BUILTIN_MODELS, DEFAULT_MODEL_ID, ModelManifest, model_manifest
from .frames import decode_frame, encode_frame

STATUS_TOPIC = "vision/detector/status"
REQUEST_TOPIC = "vision/detector/request"
RESULT_TOPIC = "vision/detector/result"
STATUS_SCHEMA = "desk_buddy.vision.status.v1"
REQUEST_SCHEMA = "desk_buddy.vision.detect-request.v1"
READY_TIMEOUT_MS = 300_000
DETECTION_TIMEOUT_MS = 120_000


@dataclass(frozen=True)
class ModelState:
    manifest: ModelManifest
    installed: bool
    selected: bool
    active: bool
    running: bool


@dataclass(frozen=True)
class VisionState:
    selected_model_id: str
    active_model_id: str = ""
    process_state: str = "stopped"
    mqtt_state: str = "offline"
    operation: str = ""
    phase: str = ""
    progress_message: str = ""
    progress_current: int | None = None
    progress_total: int | None = None
    error: str = ""
    device: str = ""
    busy: bool = False
    start_with_studio: bool = False
    pending_detection: str = ""

    @property
    def ready(self) -> bool:
        return self.process_state == "running" and self.mqtt_state in {"ready", "busy"}

    def as_dict(self) -> dict:
        return asdict(self)


class _MqttBridge(QObject):
    status_received = Signal(object)
    result_received = Signal(str, object)
    raw_received = Signal(str, bytes)


class VisionServiceManager(QObject):
    """Own the local detector while keeping all image traffic on MQTT."""

    changed = Signal(object)
    detection_result = Signal(object)
    photo_received = Signal(bytes)

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        preferences: Settings | None = None,
        client=None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.root = Path(root) if root is not None else vision_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.preferences = preferences or settings()
        selected = self.preferences.get(keys.VISION_SELECTED_MODEL)
        active = self.preferences.get(keys.VISION_ACTIVE_MODEL)
        if model_manifest(selected) is None:
            selected = DEFAULT_MODEL_ID
        if active and model_manifest(active) is None:
            active = ""
        self._state = VisionState(
            selected_model_id=selected,
            active_model_id=active,
            start_with_studio=self.preferences.get(keys.VISION_AUTO_START),
        )
        self.environment = dict(os.environ if environment is None else environment)
        self.client = client or mqtt_client()
        self._bridge = _MqttBridge(self)
        self._bridge.status_received.connect(self._on_status, Qt.QueuedConnection)
        self._bridge.result_received.connect(self._on_result, Qt.QueuedConnection)
        self._bridge.raw_received.connect(self._on_raw, Qt.QueuedConnection)
        self._status_unsubscribe = self.client.subscribe(
            STATUS_TOPIC, lambda _topic, body: self._bridge.status_received.emit(body)
        )
        self._result_unsubscribe = self.client.subscribe(
            RESULT_TOPIC, lambda topic, body: self._bridge.result_received.emit(topic, body)
        )
        self._robot_unsubscribes: list[Callable[[], None]] = []
        self._operation_process: QProcess | None = None
        self._worker_process: QProcess | None = None
        self._step_finished = False
        self._last_process_error = ""
        self._output_buffers: dict[int, str] = {}
        self._uv: Path | None = None
        self._worker: Path | None = None
        self._runtime_staging: Path | None = None
        self._model_staging: Path | None = None
        self._install_config: Path | None = None
        self._launch_config: Path | None = None
        self._credentials: VisionCredentials | None = None
        self._expected_launch_id = ""
        self._expected_model_id = ""
        self._switch_previous: tuple[str, bool] | None = None
        self._rollback_error = ""
        self._is_rollback = False
        self._pending_jpeg = b""
        self._pending_source = ""
        self._autostart_attempted = False
        self._ready_timer = QTimer(self)
        self._ready_timer.setSingleShot(True)
        self._ready_timer.timeout.connect(self._ready_timed_out)
        self._detection_timer = QTimer(self)
        self._detection_timer.setSingleShot(True)
        self._detection_timer.timeout.connect(self._detection_timed_out)

    @property
    def state(self) -> VisionState:
        return self._state

    @property
    def catalog(self) -> tuple[ModelManifest, ...]:
        return BUILTIN_MODELS

    @property
    def selected(self) -> ModelManifest:
        return model_manifest(self._state.selected_model_id) or BUILTIN_MODELS[0]

    @property
    def active(self) -> ModelManifest | None:
        return model_manifest(self._state.active_model_id)

    def models(self) -> tuple[ModelState, ...]:
        return tuple(ModelState(
            manifest=item,
            installed=self.is_installed(item.model_id),
            selected=item.model_id == self._state.selected_model_id,
            active=item.model_id == self._state.active_model_id,
            running=item.model_id == self._state.active_model_id and self._state.ready,
        ) for item in BUILTIN_MODELS)

    def select_model(self, model_id: str) -> None:
        if model_manifest(model_id) is None:
            raise ValueError("model is not in Studio's curated catalog")
        if model_id == self._state.selected_model_id:
            return
        self.preferences.set(keys.VISION_SELECTED_MODEL, model_id)
        self.preferences.sync()
        self._replace(selected_model_id=model_id, error="")

    def set_start_with_studio(self, enabled: bool) -> None:
        self.preferences.set(keys.VISION_AUTO_START, bool(enabled))
        self.preferences.sync()
        self._autostart_attempted = False
        self._replace(start_with_studio=bool(enabled))

    def report_error(self, message: str) -> None:
        """Expose a user-input error without letting pages mutate state."""
        self._replace(error=message)

    def is_installed(self, model_id: str) -> bool:
        manifest = model_manifest(model_id)
        if manifest is None:
            return False
        return self._runtime_python().exists() and self._snapshot_installed(manifest)

    def _snapshot_installed(self, manifest: ModelManifest) -> bool:
        marker = self._marker_path(manifest)
        if not marker.exists() or not self._model_path(manifest).is_dir():
            return False
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return (
            value.get("model_id") == manifest.model_id
            and value.get("revision") == manifest.revision
        )

    def install_and_use(self) -> None:
        if self._state.operation or self._state.pending_detection:
            if self._state.pending_detection:
                self._replace(error="Wait for the current preview to finish before changing models.")
            return
        manifest = self.selected
        if self.is_installed(manifest.model_id):
            self.use_and_start()
            return
        try:
            self._uv, self._worker = materialize_bootstrap(self.root)
        except Exception as exc:
            self._replace(error=f"Runtime setup: {exc}")
            return
        token = uuid.uuid4().hex[:10]
        self._switch_previous = (self._state.active_model_id, self._state.ready)
        self._runtime_staging = self.root / "runtimes" / f".zero-shot-{token}.installing"
        self._model_staging = self.root / "models" / f".{manifest.model_id}-{token}.installing"
        self._clean_path(self._runtime_staging)
        self._clean_path(self._model_staging)
        self._replace(
            operation="install",
            # Installing is independent of the currently active worker. This
            # preserves truthful readiness (and preview use) while another
            # model downloads in the background.
            process_state="running" if self._worker_running() else "stopped",
            phase="runtime",
            progress_message="Preparing isolated Vision runtime…",
            progress_current=None,
            progress_total=None,
            error="",
        )
        if self._runtime_python().exists():
            self._download_model()
        else:
            self._create_runtime()

    def cancel_install(self) -> None:
        if self._state.operation != "install":
            return
        process = self._operation_process
        if process is not None and process.state() != QProcess.NotRunning:
            # Prevent the normal step callback from treating this deliberate
            # termination as an install failure while waitForFinished spins
            # Qt's event processing.
            self._replace(operation="cancelling")
            process.terminate()
            if not process.waitForFinished(3000):
                process.kill()
                process.waitForFinished(2000)
        self._cleanup_install()
        self._replace(
            operation="", process_state="running" if self._worker_running() else "stopped",
            phase="", progress_message="Installation cancelled", error="Installation cancelled.",
        )

    def use_and_start(self) -> None:
        manifest = self.selected
        if not self.is_installed(manifest.model_id):
            self.install_and_use()
            return
        if self._state.operation or self._state.pending_detection:
            if self._state.pending_detection:
                self._replace(error="Wait for the current preview to finish before changing models.")
            return
        self._switch_previous = (self._state.active_model_id, self._state.ready)
        self._activate(manifest)

    def start(self) -> None:
        manifest = self.active or self.selected
        if not self.is_installed(manifest.model_id):
            self._replace(error="Install the selected detector before starting it.")
            return
        if self._worker_running() or self._state.operation:
            return
        self._switch_previous = (self._state.active_model_id, False)
        self._activate(manifest)

    def start_enabled(self) -> None:
        if (
            not self._state.start_with_studio
            or not self._state.active_model_id
            or self._autostart_attempted
            or self._worker_running()
        ):
            return
        _host, port = studio_endpoint()
        if not port:
            return
        self._autostart_attempted = True
        self.start()

    def stop(self) -> None:
        cancelling_start = self._state.operation in {"start", "rollback"}
        self._ready_timer.stop()
        self._detection_timer.stop()
        self._expected_launch_id = ""
        self._expected_model_id = ""
        had_detection = bool(self._state.pending_detection)
        self._pending_jpeg = b""
        self._pending_source = ""
        self._clear_robot_subscriptions()
        process = self._worker_process
        if process is not None and process.state() != QProcess.NotRunning:
            self._replace(process_state="stopping")
            process.terminate()
            if not process.waitForFinished(8000):
                process.kill()
                process.waitForFinished(3000)
        self._worker_process = None
        self._remove_launch_config()
        if cancelling_start:
            self._switch_previous = None
            self._rollback_error = ""
            self._is_rollback = False
            self._cleanup_install()
        elif self._state.operation == "install" and self._switch_previous is not None:
            # The download may continue, but an explicitly stopped previous
            # worker must not be restarted as a rollback side effect.
            self._switch_previous = (self._switch_previous[0], False)
        self._replace(
            process_state="stopped", mqtt_state="offline", busy=False, device="",
            operation="" if cancelling_start else self._state.operation,
            phase="" if cancelling_start else self._state.phase,
            progress_message="" if cancelling_start else self._state.progress_message,
            pending_detection="",
            error="Detection cancelled because the detector stopped." if had_detection else self._state.error,
        )

    def remove_selected(self) -> None:
        manifest = self.selected
        if self._state.operation:
            return
        if manifest.model_id == self._state.active_model_id and self._worker_running():
            self.stop()
        self._clean_path(self._model_path(manifest))
        self._marker_path(manifest).unlink(missing_ok=True)
        values = {"error": ""}
        if manifest.model_id == self._state.active_model_id:
            self.preferences.set(keys.VISION_ACTIVE_MODEL, "")
            self.preferences.sync()
            values["active_model_id"] = ""
        self._replace(**values)

    def detect_local(self, jpeg: bytes, prompt: str) -> str:
        clean = prompt.strip()
        if not self._can_detect(clean):
            return ""
        action_id = uuid.uuid4().hex
        try:
            active = self.active
            payload = encode_frame({
                "schema": REQUEST_SCHEMA,
                "sender": "studio",
                "action_id": action_id,
                "prompt": clean,
                "model_name": active.source if active is not None else self._state.active_model_id,
                "use_model": False,
            }, jpeg)
        except ValueError as exc:
            self._replace(error=f"Local photo: {exc}")
            return ""
        self.client.reconcile()
        self._pending_jpeg = jpeg
        self._pending_source = "local"
        self._begin_detection(action_id)
        if not self.client.publish_raw(REQUEST_TOPIC, payload, qos=1):
            self._detection_timer.stop()
            self._replace(
                pending_detection="",
                error=f"Could not publish the photo: {self.client.status}",
            )
            return ""
        self.photo_received.emit(jpeg)
        return action_id

    def detect_robot(self, robot: str, prompt: str) -> str:
        clean = prompt.strip()
        if not robot or not self._can_detect(clean):
            if not robot:
                self._replace(error="Choose a configured robot first.")
            return ""
        self._clear_robot_subscriptions()
        topic = f"{robot}/test"
        self._robot_unsubscribes.extend((
            self.client.subscribe_raw(
                topic, lambda name, payload: self._bridge.raw_received.emit(name, payload)
            ),
            self.client.subscribe(
                topic, lambda name, body: self._bridge.result_received.emit(name, body)
            ),
        ))
        self.client.reconcile()
        if not self.client.running:
            self._replace(error=f"Could not request a robot photo: {self.client.status}")
            self._clear_robot_subscriptions()
            return ""
        action_id = uuid.uuid4().hex
        active = self.active
        self._pending_jpeg = b""
        self._pending_source = topic
        self._begin_detection(action_id)
        self.client.publish(topic, {
            "sender": "studio",
            "action_id": action_id,
            "action": "detect_object",
            "phrase": clean,
            "use_model": False,
            "model_name": active.source if active is not None else self._state.active_model_id,
        }, qos=1)
        return action_id

    def shutdown(self) -> None:
        if self._state.operation == "install":
            self.cancel_install()
        self.stop()
        self._clear_robot_subscriptions()
        self._status_unsubscribe()
        self._result_unsubscribe()

    # ---- install sequence -------------------------------------------------
    def _create_runtime(self) -> None:
        assert self._uv is not None and self._runtime_staging is not None
        self._replace(progress_message=f"Downloading managed Python {PYTHON_VERSION} and creating the environment…")
        self._run_step(
            self._uv,
            ["venv", "--python", PYTHON_VERSION, "--managed-python", str(self._runtime_staging)],
            self._install_dependencies,
        )

    def _install_dependencies(self) -> None:
        assert self._uv is not None and self._runtime_staging is not None
        self._replace(phase="dependencies", progress_message="Installing Torch, Transformers, Pillow, and MQTT support…")
        self._run_step(
            self._uv,
            [
                "pip", "install", "--python", str(self._python_in(self._runtime_staging)),
                "--torch-backend", "auto", *RUNTIME_REQUIREMENTS,
            ],
            self._promote_runtime,
        )

    def _promote_runtime(self) -> None:
        assert self._runtime_staging is not None
        target = self._runtime_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self._clean_path(self._runtime_staging)
        else:
            os.replace(self._runtime_staging, target)
        self._runtime_staging = None
        self._download_model()

    def _download_model(self) -> None:
        assert self._worker is not None and self._model_staging is not None
        manifest = self.selected
        if self._snapshot_installed(manifest):
            self._clean_path(self._model_staging)
            self._model_staging = None
            self._replace(
                phase="model",
                progress_message=f"Reusing the existing pinned {manifest.name} snapshot…",
            )
            self._provision_model()
            return
        self._model_staging.mkdir(parents=True, exist_ok=True)
        self._replace(
            phase="model",
            progress_message=f"Downloading {manifest.name} ({manifest.size_mb} MB model weights)…",
        )
        self._install_config = self._write_config(
            manifest, cache_dir=self._model_staging, credentials=None, launch_id="install",
        )
        self._run_step(
            self._runtime_python(),
            [str(self._worker), "install", "--config", str(self._install_config)],
            self._provision_model,
        )

    def _provision_model(self) -> None:
        """Resolve restricted broker access before committing the snapshot."""
        manifest = self.selected
        self._replace(
            phase="broker",
            progress_message="Provisioning the detector's restricted MQTT account…",
        )
        try:
            self._credentials = ensure_access(self.environment)
        except Exception as exc:
            self._install_failed(f"MQTT account setup: {exc}")
            return
        if self._model_staging is None:
            self._replace(progress_message="Model snapshot verified; preparing its MQTT service…")
            self._activate(manifest)
        else:
            self._promote_model(manifest)

    def _promote_model(self, manifest: ModelManifest | None = None) -> None:
        assert self._model_staging is not None
        manifest = manifest or self.selected
        target = self._model_path(manifest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self._clean_path(target)
        os.replace(self._model_staging, target)
        self._model_staging = None
        self._write_json(self._marker_path(manifest), {
            "model_id": manifest.model_id,
            "source": manifest.source,
            "revision": manifest.revision,
            "installed_at": time.time(),
        })
        self._replace(progress_message="Model installed; preparing its MQTT service…")
        self._activate(manifest)

    def _run_step(self, program: Path, arguments: list[str], on_success: Callable[[], None]) -> None:
        process = QProcess(self)
        self._operation_process = process
        self._step_finished = False
        self._last_process_error = ""
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.setProcessEnvironment(self._process_environment(include_credentials=False))
        process.readyReadStandardOutput.connect(lambda: self._read_process_output(process, progress=True))

        def finished(code: int, _status) -> None:
            if self._step_finished:
                return
            self._step_finished = True
            self._read_process_output(process, progress=True, final=True)
            self._operation_process = None
            if self._state.operation != "install":
                return
            if code == 0:
                on_success()
            else:
                detail = self._last_process_error or f"command exited with status {code}"
                kind = {
                    "runtime": "Runtime setup",
                    "dependencies": "Runtime dependency installation",
                    "model": "Model download",
                }.get(self._state.phase, "Installation")
                self._install_failed(f"{kind} failed: {detail}. See the Vision service log.")

        def failed(error) -> None:
            if self._step_finished or error != QProcess.FailedToStart:
                return
            self._step_finished = True
            self._operation_process = None
            self._install_failed(f"Could not start the installer: {process.errorString()}")

        process.finished.connect(finished)
        process.errorOccurred.connect(failed)
        process.start(str(program), arguments)

    def _install_failed(self, message: str) -> None:
        self._cleanup_install()
        self._replace(
            operation="", phase="", process_state="running" if self._worker_running() else "stopped",
            progress_message="", error=message,
        )

    def _cleanup_install(self) -> None:
        for path in (self._runtime_staging, self._model_staging):
            if path is not None:
                self._clean_path(path)
        if self._install_config is not None:
            self._install_config.unlink(missing_ok=True)
        self._runtime_staging = None
        self._model_staging = None
        self._install_config = None
        self._operation_process = None

    # ---- worker lifecycle -------------------------------------------------
    def _activate(self, manifest: ModelManifest) -> None:
        try:
            self._uv, self._worker = materialize_bootstrap(self.root)
            credentials = ensure_access(self.environment)
        except Exception as exc:
            message = f"MQTT access: {exc}"
            if self._is_rollback and self._rollback_error:
                message = f"{self._rollback_error} Rollback also failed: {message}"
            self._cleanup_install()
            self._switch_previous = None
            self._is_rollback = False
            self._rollback_error = ""
            self._replace(
                operation="",
                process_state="running" if self._worker_running() else "stopped",
                phase="", progress_message="", error=message,
            )
            return
        self._credentials = credentials
        if self._worker_running():
            self.stop()
        launch_id = uuid.uuid4().hex
        config = self._write_config(
            manifest, cache_dir=self._model_path(manifest),
            credentials=credentials, launch_id=launch_id,
        )
        self._launch_config = config
        process = QProcess(self)
        self._worker_process = process
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.setProcessEnvironment(self._process_environment(include_credentials=True))
        process.readyReadStandardOutput.connect(lambda: self._read_process_output(process, progress=False))
        process.finished.connect(self._worker_finished)
        process.errorOccurred.connect(self._worker_error)
        self._last_process_error = ""
        self._expected_launch_id = launch_id
        self._expected_model_id = manifest.model_id
        self.client.reconcile()
        self._replace(
            operation="start", process_state="starting", mqtt_state="waiting",
            phase="startup", progress_message=f"Loading {manifest.name}…", error="",
            busy=False, device="",
        )
        process.start(str(self._runtime_python()), [str(self._worker), "serve", "--config", str(config)])
        self._ready_timer.start(READY_TIMEOUT_MS)

    def _worker_finished(self, code: int, _status) -> None:
        process = self.sender()
        if process is not self._worker_process:
            return
        self._read_process_output(process, progress=False, final=True)
        self._worker_process = None
        if self._state.process_state in {"stopping", "stopped"}:
            self._remove_launch_config()
            return
        self._ready_timer.stop()
        message = self._last_process_error or f"Detector process exited with status {code}."
        message += " See the Vision service log."
        self._remove_launch_config()
        if self._state.operation == "start" and not self._is_rollback:
            self._switch_failed(message)
        elif self._is_rollback:
            original = self._rollback_error
            self._switch_previous = None
            self._rollback_error = ""
            self._is_rollback = False
            self._replace(
                operation="", process_state="error", mqtt_state="offline",
                error=f"{original} Rollback also failed: {message}" if original else message,
            )
        else:
            self._replace(operation="", process_state="error", mqtt_state="offline", error=message)

    def _worker_error(self, error) -> None:
        if error != QProcess.FailedToStart or self.sender() is not self._worker_process:
            return
        self._ready_timer.stop()
        message = f"Could not start the detector: {self._worker_process.errorString()}"
        self._worker_process = None
        self._remove_launch_config()
        if not self._is_rollback:
            self._switch_failed(message)
        else:
            original = self._rollback_error
            self._switch_previous = None
            self._rollback_error = ""
            self._is_rollback = False
            self._replace(
                operation="", process_state="error", mqtt_state="offline",
                error=f"{original} Rollback also failed: {message}" if original else message,
            )

    def _ready_timed_out(self) -> None:
        self._switch_failed(
            f"{self._expected_model_id} did not advertise the expected model and revision within five minutes."
        )

    def _switch_failed(self, message: str) -> None:
        self._ready_timer.stop()
        process = self._worker_process
        self._worker_process = None
        if process is not None and process.state() != QProcess.NotRunning:
            process.terminate()
            if not process.waitForFinished(5000):
                process.kill()
                process.waitForFinished(3000)
        self._remove_launch_config()
        previous, was_running = self._switch_previous or ("", False)
        self._cleanup_install()
        if previous and was_running and self.is_installed(previous):
            previous_manifest = model_manifest(previous)
            if previous_manifest is not None:
                self._rollback_error = message
                self._is_rollback = True
                self._replace(error=message, operation="rollback", mqtt_state="offline")
                self._activate(previous_manifest)
                return
        self._switch_previous = None
        self._replace(
            operation="", process_state="error", mqtt_state="offline",
            phase="", progress_message="", error=message,
        )

    # ---- MQTT ------------------------------------------------------------
    def _on_status(self, body: dict) -> None:
        if body.get("schema") != STATUS_SCHEMA or body.get("service_id") != "detector":
            return
        if body.get("launch_id") != self._expected_launch_id:
            return
        manifest = model_manifest(self._expected_model_id)
        if (
            manifest is None
            or body.get("model_id") != manifest.source
            or body.get("revision") != manifest.revision
        ):
            return
        mqtt_state = str(body.get("state") or "offline")
        ready = bool(body.get("ready")) and mqtt_state in {"ready", "busy"}
        values = {
            "mqtt_state": mqtt_state,
            "busy": bool(body.get("busy")),
            "device": str(body.get("device") or ""),
        }
        if ready:
            self._ready_timer.stop()
            self.preferences.set(keys.VISION_ACTIVE_MODEL, manifest.model_id)
            self.preferences.sync()
            error = self._rollback_error if self._is_rollback else ""
            values.update({
                "active_model_id": manifest.model_id,
                "operation": "",
                "process_state": "running",
                "phase": "",
                "progress_message": "",
                "error": error,
            })
            self._switch_previous = None
            self._rollback_error = ""
            self._is_rollback = False
            self._cleanup_install()
        elif mqtt_state == "error":
            values["error"] = str(body.get("last_error") or "The worker reported a startup error.")
        self._replace(**values)

    def _on_raw(self, topic: str, payload: bytes) -> None:
        if not self._state.pending_detection or topic != self._pending_source:
            return
        try:
            frame = decode_frame(payload)
        except ValueError:
            return
        action_id = str(frame.metadata.get("action_id") or "")
        if action_id != self._state.pending_detection:
            return
        self._pending_jpeg = frame.jpeg
        self.photo_received.emit(frame.jpeg)

    def _on_result(self, topic: str, body: dict) -> None:
        action_id = str(body.get("action_id") or "")
        if not self._state.pending_detection or action_id != self._state.pending_detection:
            return
        if topic not in {RESULT_TOPIC, self._pending_source}:
            return
        if body.get("sender") != "visual_ai" or body.get("status") not in {"completed", "failed"}:
            return
        self._detection_timer.stop()
        self._replace(pending_detection="", busy=False)
        self.detection_result.emit(dict(body))
        self._clear_robot_subscriptions()

    def _begin_detection(self, action_id: str) -> None:
        self._replace(pending_detection=action_id, error="")
        self._detection_timer.start(DETECTION_TIMEOUT_MS)

    def _detection_timed_out(self) -> None:
        if not self._state.pending_detection:
            return
        self._replace(
            pending_detection="",
            error="Detection timed out after 120 seconds. It was not retried.",
        )
        self._clear_robot_subscriptions()

    def _can_detect(self, prompt: str) -> bool:
        if not prompt:
            self._replace(error="Enter what the detector should look for.")
            return False
        if not self._state.ready:
            self._replace(error="Start an installed detector and wait for MQTT-ready status first.")
            return False
        if self._state.pending_detection:
            self._replace(error="A detection is already in progress.")
            return False
        if self._state.operation:
            self._replace(error="Wait for the current Vision operation to finish.")
            return False
        return True

    # ---- paths/config/logs -----------------------------------------------
    def _runtime_path(self) -> Path:
        return self.root / "runtimes" / f"zero-shot-{runtime_fingerprint()}"

    def _runtime_python(self) -> Path:
        return self._python_in(self._runtime_path())

    @staticmethod
    def _python_in(runtime: Path) -> Path:
        return runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def _model_path(self, manifest: ModelManifest) -> Path:
        return self.root / "models" / manifest.model_id / manifest.revision

    def _marker_path(self, manifest: ModelManifest) -> Path:
        return self.root / "markers" / f"{manifest.model_id}-{manifest.revision}.json"

    def _write_config(
        self,
        manifest: ModelManifest,
        *,
        cache_dir: Path,
        credentials: VisionCredentials | None,
        launch_id: str,
    ) -> Path:
        config = {
            "schema": "desk_buddy.vision.worker-config.v1",
            "launch_id": launch_id,
            "mqtt": {
                "host": credentials.host if credentials else "127.0.0.1",
                "port": credentials.port if credentials else 1883,
                "username_env": USERNAME_ENV,
                "password_env": PASSWORD_ENV,
            },
            "robot_topics": list(credentials.topics[1:] if credentials else ()),
            "model": {**manifest.as_dict(), "cache_dir": str(cache_dir)},
        }
        path = self.root / "configs" / f"detector-{launch_id}.json"
        self._write_json(path, config)
        return path

    def _process_environment(self, *, include_credentials: bool) -> QProcessEnvironment:
        environment = QProcessEnvironment()
        for name, value in self.environment.items():
            if name in {USERNAME_ENV, PASSWORD_ENV}:
                continue
            environment.insert(str(name), str(value))
        environment.insert("PYTHONUNBUFFERED", "1")
        environment.insert("UV_PYTHON_INSTALL_DIR", str(self.root / "python"))
        environment.insert("UV_PYTHON_BIN_DIR", str(self.root / "python-bin"))
        environment.insert("UV_CACHE_DIR", str(self.root / "uv-cache"))
        environment.insert("UV_TORCH_BACKEND", "auto")
        environment.insert("UV_PYTHON_INSTALL_REGISTRY", "0")
        if include_credentials and self._credentials is not None:
            environment.insert(USERNAME_ENV, self._credentials.username)
            environment.insert(PASSWORD_ENV, self._credentials.password)
        return environment

    def _read_process_output(
        self, process: QProcess, *, progress: bool, final: bool = False,
    ) -> None:
        data = bytes(process.readAllStandardOutput())
        key = id(process)
        if not data and not (final and self._output_buffers.get(key)):
            return
        if data:
            self._append_log(data)
        combined = self._output_buffers.pop(key, "") + data.decode("utf-8", errors="replace")
        lines = combined.split("\n")
        if not final:
            self._output_buffers[key] = lines.pop()
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                self._last_process_error = raw[-500:]
                continue
            if event.get("event") == "progress" and progress:
                self._replace(
                    phase=str(event.get("phase") or self._state.phase),
                    progress_message=str(event.get("message") or self._state.progress_message),
                    progress_current=event.get("current"),
                    progress_total=event.get("total"),
                )
            elif event.get("event") == "error":
                self._last_process_error = str(event.get("message") or "Vision worker failed")

    def _append_log(self, data: bytes) -> None:
        path = self.root / "logs" / "detector.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Credentials never form part of commands/config output. Redact once
        # more at this boundary in case a dependency prints its environment.
        text = data.decode("utf-8", errors="replace")
        if self._credentials and self._credentials.password:
            text = text.replace(self._credentials.password, "[redacted]")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)

    def _worker_running(self) -> bool:
        return self._worker_process is not None and self._worker_process.state() != QProcess.NotRunning

    def _clear_robot_subscriptions(self) -> None:
        for unsubscribe in self._robot_unsubscribes:
            unsubscribe()
        self._robot_unsubscribes.clear()

    def _remove_launch_config(self) -> None:
        if self._launch_config is not None:
            self._launch_config.unlink(missing_ok=True)
        self._launch_config = None

    def _clean_path(self, path: Path) -> None:
        candidate = path.resolve()
        root = self.root.resolve()
        if candidate == root or root not in candidate.parents:
            raise ValueError("refusing to remove a path outside managed Vision data")
        if candidate.is_dir():
            shutil.rmtree(candidate)
        elif candidate.exists():
            candidate.unlink()

    @staticmethod
    def _write_json(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)

    def _replace(self, **values) -> None:
        current = self._state.as_dict()
        current.update(values)
        replacement = VisionState(**current)
        if replacement == self._state:
            return
        self._state = replacement
        self.changed.emit(self._state)
