"""First-class local lifecycle management for MQTT vision workers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from PySide6.QtCore import QObject, Signal

from ....storage.user_config import keys
from ....storage.user_config.settings import Settings, settings
from ..access import CredentialStore, VisionAccessManager
from ..manifests import (
    BUILTIN_MANIFESTS,
    DEPENDENCY_PROFILES,
    ProviderManifestV1,
    imported_manifests,
    load_provider_manifest,
)

MANAGED_FAMILIES = ("detection", "depth", "mlp", "trainer")
FAMILY_JOB_KIND = {
    "detection": "zero_shot.infer",
    "depth": "depth.infer",
    "mlp": "ik_model.infer",
    "trainer": "ik_model.train",
}
FAMILY_WORKER = {"mlp": "ik-inference-1", "trainer": "ik-trainer-1"}
FAMILY_ENTRY_POINT = {
    "mlp": "studio.services.vision.model_builder.inference_worker",
    "trainer": "studio.services.vision.model_builder.training_worker",
}
FAMILY_REQUIREMENTS = {
    "detection": "studio/services/vision/zero_shot/requirements.txt",
    "depth": "studio/services/vision/depth/requirements.txt",
    "mlp": "studio/services/vision/model_builder/requirements.txt",
    "trainer": "studio/services/vision/model_builder/requirements.txt",
}
AUTOSTART_KEYS = {
    "detection": keys.VISION_DETECTOR_AUTOSTART,
    "depth": keys.VISION_DEPTH_AUTOSTART,
    "mlp": keys.VISION_MLP_AUTOSTART,
    "trainer": keys.VISION_TRAINER_AUTOSTART,
}
CANDIDATE_KEYS = {
    "detection": keys.VISION_DETECTOR_CANDIDATE,
    "depth": keys.VISION_DEPTH_CANDIDATE,
    "mlp": keys.VISION_MLP_CANDIDATE,
}


@dataclass
class ManagedServiceState:
    family: str
    candidate_model_id: str = ""
    active_model_id: str = ""
    active_worker_id: str = ""
    installed: bool = False
    runtime_installed: bool = False
    process_state: str = "stopped"
    mqtt_state: str = "offline"
    operation: str = ""
    progress: str = ""
    error: str = ""
    start_with_studio: bool = False
    log_tail: str = ""
    remote: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class VisionServiceManager(QObject):
    """Owns only local processes; all worker data still crosses MQTT."""

    changed = Signal(object)

    def __init__(
        self,
        root: str | Path,
        *,
        repository_root: str | Path | None = None,
        preferences: Settings | None = None,
        environment: Mapping[str, str] | None = None,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        access: VisionAccessManager | None = None,
    ) -> None:
        super().__init__()
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.repository_root = Path(repository_root or Path(__file__).parents[4]).resolve()
        self.preferences = preferences or settings()
        self.environment = os.environ if environment is None else environment
        self.process_factory = process_factory
        self.manifest_dir = self.root / "manifests"
        self.runtime_dir = self.root / "runtimes"
        self.model_dir = self.root / "models"
        self.config_dir = self.root / "configs"
        self.log_dir = self.root / "logs"
        self.marker_dir = self.root / "markers"
        for directory in (self.manifest_dir, self.runtime_dir, self.model_dir, self.config_dir, self.log_dir, self.marker_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.access = access or VisionAccessManager(
            self.root, preferences=self.preferences, environment=self.environment,
        )
        self.credentials = self.access.credentials
        self._catalog: tuple[ProviderManifestV1, ...] = ()
        self._states: dict[str, ManagedServiceState] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._log_handles: dict[str, Any] = {}
        self._install_process: subprocess.Popen | None = None
        self._install_cancel = threading.Event()
        self._install_lock = threading.Lock()
        self._pending_switch: dict[str, tuple[ProviderManifestV1, str, str, bool, float]] = {}
        self.on_activate: Callable[[str, str, str], None] | None = None
        self.refresh_catalog()
        for family in MANAGED_FAMILIES:
            candidate = self.preferences.get(CANDIDATE_KEYS[family]) if family in CANDIDATE_KEYS else ""
            active_model, active_worker = self._saved_active(family)
            active_is_managed = bool(active_model and self.manifest(active_model, family))
            self._states[family] = ManagedServiceState(
                family=family,
                candidate_model_id=candidate,
                active_model_id=active_model,
                active_worker_id=active_worker or FAMILY_WORKER.get(family, ""),
                start_with_studio=self.preferences.get(AUTOSTART_KEYS[family]),
                remote=bool(active_model and not active_is_managed),
            )
            self._refresh_install_state(family)

    @property
    def catalog(self) -> tuple[ProviderManifestV1, ...]:
        return self._catalog

    @property
    def has_start_with_studio(self) -> bool:
        return any(state.start_with_studio for state in self._states.values())

    def manifests(self, family: str) -> tuple[ProviderManifestV1, ...]:
        return tuple(item for item in self._catalog if item.family == family)

    def manifest(self, model_id: str, family: str | None = None) -> ProviderManifestV1 | None:
        return next((item for item in self._catalog if item.model_id == model_id and (family is None or item.family == family)), None)

    def refresh_catalog(self) -> None:
        imported = imported_manifests(self.manifest_dir)
        seen: set[tuple[str, str, str]] = set()
        values = []
        for manifest in (*BUILTIN_MANIFESTS, *imported):
            key = (manifest.family, manifest.model_id, manifest.worker_id)
            if key in seen:
                continue
            seen.add(key)
            values.append(manifest)
        self._catalog = tuple(values)

    def import_manifest(self, path: str | Path) -> ProviderManifestV1:
        manifest = load_provider_manifest(path)
        if any(item.model_id == manifest.model_id for item in self._catalog):
            raise ValueError(f"a provider called {manifest.model_id} already exists")
        if any(item.worker_id == manifest.worker_id for item in self._catalog):
            raise ValueError(f"worker route {manifest.worker_id} is already assigned")
        target = self.manifest_dir / f"{manifest.model_id}.toml"
        target.write_bytes(Path(path).read_bytes())
        target.chmod(0o600)
        self.refresh_catalog()
        self.changed.emit(self.snapshot())
        return manifest

    def state(self, family: str) -> ManagedServiceState:
        if family not in self._states:
            raise ValueError(f"unknown managed family: {family}")
        self._refresh_install_state(family)
        self._states[family].log_tail = self._tail(family)
        return self._states[family]

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {family: self.state(family).as_dict() for family in MANAGED_FAMILIES}

    def set_candidate(self, family: str, model_id: str) -> None:
        state = self.state(family)
        if family in {"detection", "depth"} and self.manifest(model_id, family) is None:
            raise ValueError(f"{model_id} is not in the {family} catalog")
        state.candidate_model_id = model_id
        if family in CANDIDATE_KEYS:
            self.preferences.set(CANDIDATE_KEYS[family], model_id)
            self.preferences.sync()
        self._refresh_install_state(family)
        # Network and Vision share the access manager; this tells Network that
        # the selected worker identity in its bulk plan may have changed.
        self.access.changed.emit()
        self.changed.emit(self.snapshot())

    def set_start_with_studio(self, family: str, enabled: bool) -> None:
        state = self.state(family)
        state.start_with_studio = bool(enabled)
        self.preferences.set(AUTOSTART_KEYS[family], bool(enabled))
        self.preferences.sync()
        self.changed.emit(self.snapshot())

    def set_external_active(self, family: str, model_id: str, worker_id: str) -> None:
        """Record a remote route after the controller validates its live status."""
        if family not in {"detection", "depth"}:
            raise ValueError("remote providers are supported only for detection and depth")
        if self.running(family):
            self.stop(family)
        state = self.state(family)
        state.active_model_id = model_id
        state.active_worker_id = worker_id
        state.process_state = "remote"
        state.mqtt_state = "ready"
        state.remote = True
        state.start_with_studio = False
        state.error = ""
        self.preferences.set(AUTOSTART_KEYS[family], False)
        self._save_active(family, model_id, worker_id)
        self.changed.emit(self.snapshot())

    def install_candidate(self, family: str, *, repair: bool = False) -> None:
        state = self.state(family)
        manifest = self.manifest(state.candidate_model_id, family) if family in {"detection", "depth"} else None
        if family in {"detection", "depth"} and manifest is None:
            raise ValueError("choose a catalog model first")
        if self.running(family):
            raise RuntimeError("stop this managed service before changing its installation")
        if not self._install_lock.acquire(blocking=False):
            raise RuntimeError("another vision service installation is already running")
        self._install_cancel.clear()
        try:
            threading.Thread(
                target=self._install,
                args=(family, manifest, repair),
                name=f"vision-install-{family}",
                daemon=True,
            ).start()
        except Exception:
            self._install_lock.release()
            raise

    def cancel_install(self) -> None:
        self._install_cancel.set()
        process = self._install_process
        if process is not None and process.poll() is None:
            process.terminate()

    def use_and_start(self, family: str) -> None:
        if family not in {"detection", "depth"}:
            raise ValueError("Use & Start applies to detector and depth providers")
        state = self.state(family)
        manifest = self.manifest(state.candidate_model_id, family)
        if manifest is None or not self._model_installed(manifest):
            raise RuntimeError("download this model and its runtime first")
        # Resolve/provision credentials before interrupting a working service.
        # This is deliberately a preflight only; the generated configuration
        # still contains environment variable names, never the secret values.
        try:
            self._worker_credentials(manifest.worker_id)
        except Exception as exc:
            state.error = f"MQTT access: {exc}"
            self.changed.emit(self.snapshot())
            raise
        previous_model, previous_worker = state.active_model_id, state.active_worker_id
        was_running = self.running(family)
        if was_running:
            self.stop(family)
        state.process_state = "starting"
        state.mqtt_state = "waiting"
        state.error = ""
        self._pending_switch[family] = (
            manifest, previous_model, previous_worker, was_running, time.monotonic(),
        )
        try:
            self._spawn(family, manifest)
        except Exception:
            self._pending_switch.pop(family, None)
            self._rollback(family, previous_model, previous_worker, was_running)
            raise
        self.changed.emit(self.snapshot())

    def start(self, family: str) -> None:
        state = self.state(family)
        if self.running(family):
            return
        if family in {"detection", "depth"}:
            manifest = self.manifest(state.active_model_id or state.candidate_model_id, family)
            if manifest is None or not self._model_installed(manifest):
                raise RuntimeError("the selected provider is not installed")
            self._spawn(family, manifest)
        else:
            if not self._runtime_python(family).exists():
                raise RuntimeError("set up this worker runtime first")
            self._spawn(family, None)
        state.process_state = "starting"
        state.mqtt_state = "waiting"
        state.error = ""
        self.changed.emit(self.snapshot())

    def stop(self, family: str) -> None:
        self._pending_switch.pop(family, None)
        process = self._processes.pop(family, None)
        state = self.state(family)
        if process is not None and process.poll() is None:
            state.process_state = "stopping"
            self.changed.emit(self.snapshot())
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self._close_log(family)
        state.process_state = "stopped"
        state.mqtt_state = "offline"
        self.changed.emit(self.snapshot())

    def restart(self, family: str) -> None:
        self.stop(family)
        self.start(family)

    def running(self, family: str) -> bool:
        process = self._processes.get(family)
        return process is not None and process.poll() is None

    def remove_candidate_download(self, family: str) -> None:
        state = self.state(family)
        manifest = self.manifest(state.candidate_model_id, family)
        if manifest is None:
            raise ValueError("choose a downloaded provider first")
        if self.running(family) and state.active_model_id == manifest.model_id:
            self.stop(family)
        self._remove_managed(self._model_path(manifest))
        marker = self._marker_path(manifest)
        if marker.exists():
            marker.unlink()
        self._refresh_install_state(family)
        self.changed.emit(self.snapshot())

    def reset_runtime(self, family: str) -> None:
        self.stop(family)
        self._remove_managed(self._runtime_path(family))
        self._refresh_install_state(family)
        self.changed.emit(self.snapshot())

    def sync_mqtt(self, workers: Iterable[Mapping[str, Any]]) -> None:
        statuses = {str(item.get("worker_id") or ""): item for item in workers}
        for family, state in self._states.items():
            worker_id = state.active_worker_id or FAMILY_WORKER.get(family, "")
            status = statuses.get(worker_id)
            ready = bool(status and status.get("ready") and FAMILY_JOB_KIND[family] in status.get("job_kinds", ()))
            if ready and state.active_model_id and family in {"detection", "depth"}:
                manifest = self.manifest(state.active_model_id, family)
                ready = any(
                    model.get("model_id") == state.active_model_id
                    and (manifest is None or str(model.get("model_version") or "") == manifest.revision)
                    for model in status.get("models", ())
                )
            state.mqtt_state = "ready" if ready else "offline"
            if ready and self.running(family):
                state.process_state = "running"

        for family, pending in tuple(self._pending_switch.items()):
            manifest, previous_model, previous_worker, was_running, _ = pending
            status = statuses.get(manifest.worker_id)
            ready = bool(
                status and status.get("ready") and manifest.kind in status.get("job_kinds", ())
                and any(
                    model.get("model_id") == manifest.model_id
                    and str(model.get("model_version") or "") == manifest.revision
                    for model in status.get("models", ())
                )
            )
            if ready:
                state = self._states[family]
                state.active_model_id = manifest.model_id
                state.active_worker_id = manifest.worker_id
                state.process_state = "running"
                state.mqtt_state = "ready"
                state.remote = False
                self._save_active(family, manifest.model_id, manifest.worker_id)
                self._pending_switch.pop(family, None)
                if self.on_activate:
                    self.on_activate(family, manifest.model_id, manifest.worker_id)
        self.changed.emit(self.snapshot())

    def tick(self, *, startup_timeout: float = 300.0) -> None:
        changed = False
        for family, process in tuple(self._processes.items()):
            code = process.poll()
            if code is not None:
                self._processes.pop(family, None)
                self._close_log(family)
                state = self._states[family]
                state.process_state = "error" if code else "stopped"
                state.mqtt_state = "offline"
                if code:
                    state.error = f"worker exited with status {code}"
                changed = True
        for family, pending in tuple(self._pending_switch.items()):
            manifest, previous_model, previous_worker, was_running, started = pending
            if time.monotonic() - started > startup_timeout or not self.running(family):
                state = self._states[family]
                error = state.error or f"{manifest.model_id} did not become MQTT-ready"
                self._pending_switch.pop(family, None)
                if self.running(family):
                    self.stop(family)
                state.error = error
                self._rollback(family, previous_model, previous_worker, was_running)
                changed = True
        if changed:
            self.changed.emit(self.snapshot())

    def start_enabled(self) -> None:
        for family in MANAGED_FAMILIES:
            if self.state(family).start_with_studio and not self.running(family):
                try:
                    self.start(family)
                except Exception as exc:
                    self._states[family].error = str(exc)
        self.changed.emit(self.snapshot())

    def stop_all(self) -> None:
        self.cancel_install()
        for family in tuple(self._processes):
            self.stop(family)

    def controller_credentials(self) -> tuple[str | None, str | None]:
        state = self.access.ensure_controller()
        return state.username or None, state.password or None

    def _install(self, family: str, manifest: ProviderManifestV1 | None, repair: bool) -> None:
        try:
            state = self._states[family]
            state.operation = "repair" if repair else "install"
            state.process_state = "installing"
            state.progress = "Preparing installation"
            state.error = ""
            runtime = self._runtime_path(family)
            staging_runtime = runtime.with_name(runtime.name + ".installing")
            cache = self._model_path(manifest) if manifest else None
            staging_cache = cache.with_name(cache.name + ".installing") if cache else None
            created_runtime = repair or not self._runtime_python(family).exists()
            try:
                if created_runtime:
                    self._remove_managed(staging_runtime)
                    state.progress = "Creating isolated Python environment"
                    self._notify()
                    self._run_install([sys.executable, "-m", "venv", str(staging_runtime)], family)
                    python = staging_runtime / "bin" / "python"
                    requirements = self.repository_root / FAMILY_REQUIREMENTS[family]
                    state.progress = "Installing worker dependencies"
                    self._notify()
                    self._run_install([str(python), "-m", "pip", "install", "-r", str(requirements)], family)
                else:
                    python = self._runtime_python(family)

                if manifest is not None:
                    self._remove_managed(staging_cache)
                    staging_cache.mkdir(parents=True, exist_ok=True)
                    config = self._write_worker_config(family, manifest, local_files_only=True)
                    state.progress = f"Downloading {manifest.model_id} at {manifest.revision[:12]}"
                    self._notify()
                    env = dict(self.environment)
                    env["HF_HOME"] = str(staging_cache)
                    env["PYTHONUNBUFFERED"] = "1"
                    self._run_install(
                        [
                            str(python), "-m", "studio.services.vision.install_model",
                            "--config", str(config), "--model-id", manifest.model_id,
                            "--install", "--accept-license",
                        ],
                        family,
                        env=env,
                    )

                if created_runtime:
                    backup = runtime.with_name(runtime.name + ".previous")
                    self._remove_managed(backup)
                    if runtime.exists():
                        runtime.replace(backup)
                    staging_runtime.replace(runtime)
                    self._remove_managed(backup)
                if manifest is not None and staging_cache is not None and cache is not None:
                    self._remove_managed(cache)
                    staging_cache.replace(cache)
                    self._write_json(self._marker_path(manifest), {
                        "model_id": manifest.model_id,
                        "worker_id": manifest.worker_id,
                        "revision": manifest.revision,
                        "adapter_id": manifest.adapter_id,
                        "installed_at": time.time(),
                    })
                state.progress = "Installation complete"
                state.process_state = "stopped"
            except Exception as exc:
                state.error = "Installation cancelled" if self._install_cancel.is_set() else str(exc)
                state.process_state = "error"
                self._remove_managed(staging_runtime)
                if staging_cache is not None:
                    self._remove_managed(staging_cache)
            finally:
                self._install_process = None
                state.operation = ""
                self._refresh_install_state(family)
                self._notify()
        finally:
            self._install_lock.release()

    def _run_install(self, command: list[str], family: str, *, env: Mapping[str, str] | None = None) -> None:
        if self._install_cancel.is_set():
            raise RuntimeError("installation cancelled")
        log = self._open_log(family, append=True)
        try:
            process = self.process_factory(
                command,
                cwd=str(self.repository_root),
                env=dict(env or self.environment),
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            self._install_process = process
            code = process.wait()
        finally:
            self._close_log(family)
        if self._install_cancel.is_set():
            raise RuntimeError("installation cancelled")
        if code:
            raise RuntimeError(f"installation command failed with status {code}; see the service log")

    def _spawn(self, family: str, manifest: ProviderManifestV1 | None) -> None:
        worker_id = manifest.worker_id if manifest else FAMILY_WORKER[family]
        config = self._write_worker_config(family, manifest, local_files_only=True)
        entry_point = manifest.entry_point if manifest else FAMILY_ENTRY_POINT[family]
        cache = self._model_path(manifest) if manifest else self.root / "models" / family
        env = self._child_environment(worker_id, cache)
        log = self._open_log(family, append=True)
        process = self.process_factory(
            [str(self._runtime_python(family)), "-m", entry_point, "--config", str(config)],
            cwd=str(self.repository_root),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        self._processes[family] = process
        state = self._states[family]
        state.active_worker_id = worker_id
        if manifest and not state.active_model_id:
            state.active_model_id = manifest.model_id

        def monitor() -> None:
            process.wait()
            self.tick()

        threading.Thread(target=monitor, name=f"vision-worker-{family}", daemon=True).start()

    def _rollback(self, family: str, previous_model: str, previous_worker: str, was_running: bool) -> None:
        state = self._states[family]
        state.active_model_id = previous_model
        state.active_worker_id = previous_worker
        state.remote = bool(previous_model and self.manifest(previous_model, family) is None)
        if was_running and previous_model:
            previous = self.manifest(previous_model, family)
            if previous and self._model_installed(previous):
                try:
                    self._spawn(family, previous)
                    state.process_state = "starting"
                    return
                except Exception as exc:
                    state.error += f"; previous worker could not be restored: {exc}"
        state.process_state = "error"

    def _worker_credentials(self, worker_id: str) -> tuple[str | None, str | None]:
        state = self.access.ensure_worker(worker_id)
        return state.username or None, state.password or None

    def _child_environment(self, worker_id: str, cache: Path) -> dict[str, str]:
        username, password = self._worker_credentials(worker_id)
        prefix = "DESK_BUDDY_MANAGED_" + _slug(worker_id).upper().replace("-", "_")
        env = dict(self.environment)
        if username:
            env[f"{prefix}_MQTT_USERNAME"] = username
        if password:
            env[f"{prefix}_MQTT_PASSWORD"] = password
        env["HF_HOME"] = str(cache)
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def _write_worker_config(
        self, family: str, manifest: ProviderManifestV1 | None, *, local_files_only: bool
    ) -> Path:
        worker_id = manifest.worker_id if manifest else FAMILY_WORKER[family]
        prefix = "DESK_BUDDY_MANAGED_" + _slug(worker_id).upper().replace("-", "_")
        host = str(self.environment.get("DESK_BUDDY_VISION_MQTT_HOST") or "127.0.0.1")
        port = int(self.environment.get("DESK_BUDDY_VISION_MQTT_PORT") or 18830)
        topic_root = str(self.environment.get("DESK_BUDDY_VISION_TOPIC_ROOT") or "desk_buddy")
        worker_kind = {"detection": "zero_shot", "depth": "depth", "mlp": "ik-inference", "trainer": "ik-training"}[family]
        lines = [
            "[mqtt]", f"host = {json.dumps(host)}", f"port = {port}", f"topic_root = {json.dumps(topic_root)}",
            f"username_env = {json.dumps(prefix + '_MQTT_USERNAME')}",
            f"password_env = {json.dumps(prefix + '_MQTT_PASSWORD')}", "",
            "[worker]", f"worker_id = {json.dumps(worker_id)}", f"worker_kind = {json.dumps(worker_kind)}",
            f"client_id = {json.dumps('desk-buddy-managed-' + worker_id)}",
            f"device = {json.dumps('cpu' if family in {'mlp', 'trainer'} else 'auto')}",
            "artifact_timeout_seconds = 300", "max_frame_bytes = 8388608", "",
        ]
        if manifest:
            lines.extend([
                f"[models.{json.dumps(manifest.model_id)}]",
                f"source = {json.dumps(manifest.source)}",
                f"revision = {json.dumps(manifest.revision)}",
                f"local_files_only = {'true' if local_files_only else 'false'}",
                f"license = {json.dumps(manifest.license)}",
                f"size_mb = {manifest.size_mb or 0}",
                "devices = [" + ", ".join(json.dumps(item) for item in manifest.devices) + "]",
            ])
            if family == "detection":
                lines.append("prompt_style = \"auto\"")
            if family == "depth":
                lines.append("native_near_is_high = true")
        else:
            lines.append("[models]")
        path = self.config_dir / f"{worker_id}.toml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        path.chmod(0o600)
        return path

    def _saved_active(self, family: str) -> tuple[str, str]:
        if family == "detection":
            return (
                str(self.environment.get("DESK_BUDDY_DETECTOR_MODEL") or self.preferences.get(keys.VISION_DETECTOR_MODEL)),
                str(self.environment.get("DESK_BUDDY_DETECTOR_WORKER") or self.preferences.get(keys.VISION_DETECTOR_WORKER)),
            )
        if family == "depth":
            return (
                str(self.environment.get("DESK_BUDDY_DEPTH_MODEL") or self.preferences.get(keys.VISION_DEPTH_MODEL)),
                str(self.environment.get("DESK_BUDDY_DEPTH_WORKER") or self.preferences.get(keys.VISION_DEPTH_WORKER)),
            )
        return "", FAMILY_WORKER.get(family, "")

    def _save_active(self, family: str, model_id: str, worker_id: str) -> None:
        if family == "detection":
            self.preferences.set(keys.VISION_DETECTOR_MODEL, model_id)
            self.preferences.set(keys.VISION_DETECTOR_WORKER, worker_id)
        elif family == "depth":
            self.preferences.set(keys.VISION_DEPTH_MODEL, model_id)
            self.preferences.set(keys.VISION_DEPTH_WORKER, worker_id)
        self.preferences.sync()

    def _refresh_install_state(self, family: str) -> None:
        state = self._states.get(family)
        if state is None:
            return
        runtime = self._runtime_python(family).exists()
        state.runtime_installed = runtime
        if family in {"detection", "depth"}:
            manifest = self.manifest(state.candidate_model_id, family)
            state.installed = bool(runtime and manifest and self._model_installed(manifest))
        else:
            state.installed = runtime

    def _model_installed(self, manifest: ProviderManifestV1) -> bool:
        marker = self._marker_path(manifest)
        if not marker.exists() or not self._model_path(manifest).exists():
            return False
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return value.get("revision") == manifest.revision and value.get("adapter_id") == manifest.adapter_id

    def _runtime_path(self, family: str) -> Path:
        return self.runtime_dir / family

    def _runtime_python(self, family: str) -> Path:
        return self._runtime_path(family) / "bin" / "python"

    def _model_path(self, manifest: ProviderManifestV1) -> Path:
        return self.model_dir / _slug(manifest.model_id) / manifest.revision

    def _marker_path(self, manifest: ProviderManifestV1) -> Path:
        return self.marker_dir / f"{_slug(manifest.model_id)}-{manifest.revision}.json"

    def _open_log(self, family: str, *, append: bool) -> Any:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        handle = (self.log_dir / f"{family}.log").open("a" if append else "w", encoding="utf-8")
        self._log_handles[family] = handle
        return handle

    def _close_log(self, family: str) -> None:
        handle = self._log_handles.pop(family, None)
        if handle is not None:
            handle.close()

    def _tail(self, family: str, count: int = 8) -> str:
        path = self.log_dir / f"{family}.log"
        if not path.exists():
            return ""
        try:
            return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:])
        except OSError:
            return ""

    def _remove_managed(self, path: Path) -> None:
        candidate = path.resolve()
        if candidate == self.root or self.root not in candidate.parents:
            raise ValueError("refusing to remove a path outside the managed vision directory")
        if candidate.is_dir():
            shutil.rmtree(candidate)
        elif candidate.exists():
            candidate.unlink()

    @staticmethod
    def _write_json(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(dict(value), separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)

    def _notify(self) -> None:
        self.changed.emit(self.snapshot())


def _slug(value: str) -> str:
    result = "".join(character if character.isalnum() or character in "-_" else "-" for character in value)
    return result[:160] or "provider"
