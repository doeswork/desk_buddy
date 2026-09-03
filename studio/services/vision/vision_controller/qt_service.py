"""Qt-safe lifecycle and signal bridge for Studio's Vision page."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Mapping

from PySide6.QtCore import QObject, QTimer, Signal

from ....storage.user_config import keys
from ....storage.user_config.settings import Settings, settings
from ...network.broker_finder import DEFAULT_PORT
from ..transport import PahoTransport
from ..model_builder import VisionStore
from ..access import VisionAccessManager, default_vision_access, vision_data_root
from .controller import ControllerConfig, VisionController
from .launcher import LocalWorkerLauncher
from .managed_services import VisionServiceManager


class VisionService(QObject):
    update = Signal(str, object)
    connection_changed = Signal(bool, str)

    def __init__(
        self, parent: QObject | None = None, *, access: VisionAccessManager | None = None
    ) -> None:
        super().__init__(parent)
        self.transport: PahoTransport | None = None
        self.controller: VisionController | None = None
        self.config = _controller_config()
        self._connecting = False
        self._generation = 0
        self._autostart_suppressed = False
        self._next_autostart_attempt = 0.0
        self.access = access or default_vision_access()
        self.store = VisionStore(_application_data_root())
        launcher_config = os.getenv("DESK_BUDDY_VISION_LOCAL_WORKERS")
        self.launcher = LocalWorkerLauncher.from_toml(launcher_config) if launcher_config else LocalWorkerLauncher()
        self.manager = VisionServiceManager(
            _application_data_root() / "services", access=self.access,
        )
        self.manager.changed.connect(lambda snapshot: self.update.emit("managed_services", snapshot))
        self.manager.on_activate = self._activate_managed_provider
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    @property
    def connected(self) -> bool:
        return self.controller is not None

    def connect_service(self) -> None:
        if self.connected or self._connecting:
            return
        self._autostart_suppressed = False
        # Re-read preferences here so a provider chosen in the UI is also the
        # route used after disconnecting and reconnecting.
        self.config = _controller_config()
        self._connecting = True
        self._generation += 1
        generation = self._generation
        threading.Thread(target=lambda: self._connect(generation), name="studio-vision-mqtt", daemon=True).start()

    def disconnect_service(self, *, suppress_autostart: bool = True) -> None:
        if suppress_autostart:
            self._autostart_suppressed = True
        self._generation += 1
        transport, self.transport = self.transport, None
        self.controller = None
        self.config = _controller_config()
        if transport is not None:
            transport.close()
        self.connection_changed.emit(False, "Disconnected")

    def close(self) -> None:
        self.disconnect_service(suppress_autostart=True)
        self.manager.stop_all()
        self.launcher.stop_all()

    def process_file(
        self,
        path: str,
        *,
        prompt: str,
        execute: bool = False,
        planner: str = "deterministic",
        learned_model_id: str = "",
    ) -> str:
        controller = self._require_controller()
        return controller.process_image(
            Path(path).read_bytes(), prompt=prompt, execute=execute,
            planner=planner, learned_model_id=learned_model_id,
        )

    def _connect(self, generation: int) -> None:
        transport: PahoTransport | None = None
        try:
            config = self.config
            managed_username, managed_password = self.manager.controller_credentials()
            transport = PahoTransport(
                host=os.getenv("DESK_BUDDY_VISION_MQTT_HOST", "127.0.0.1"),
                port=int(os.getenv("DESK_BUDDY_VISION_MQTT_PORT", str(DEFAULT_PORT))),
                client_id=os.getenv("DESK_BUDDY_VISION_MQTT_CLIENT_ID", "desk-buddy-studio-vision"),
                username=os.getenv("DESK_BUDDY_STUDIO_MQTT_USERNAME") or managed_username,
                password=os.getenv("DESK_BUDDY_STUDIO_MQTT_PASSWORD") or managed_password,
                tls=_env_bool("DESK_BUDDY_VISION_MQTT_TLS"),
                ca_file=os.getenv("DESK_BUDDY_VISION_MQTT_CA_FILE"),
            )
            controller = VisionController(config=config, transport=transport, store=self.store)
            controller.on_update = self._controller_update
            calibration = os.getenv("DESK_BUDDY_VISION_CALIBRATION")
            if calibration:
                with Path(calibration).expanduser().open(encoding="utf-8") as handle:
                    controller.set_calibration_grid(config.robot_id, json.load(handle))
            transport.set_handler(controller.handle_message)
            transport.connect(timeout=5.0)
            controller.start()
            if generation != self._generation:
                transport.close()
                return
            self.transport = transport
            self.controller = controller
            self.connection_changed.emit(True, "Connected to vision MQTT")
            self.manager.start_enabled()
        except Exception as exc:
            if transport is not None:
                transport.close()
            self.connection_changed.emit(False, str(exc))
        finally:
            self._connecting = False

    def _require_controller(self) -> VisionController:
        if self.controller is None:
            raise RuntimeError("connect Vision to MQTT first")
        return self.controller

    def _tick(self) -> None:
        self.manager.tick()
        if self.controller is not None:
            self.controller.expire_commands()
        elif not self._connecting and not self._autostart_suppressed:
            now = time.monotonic()
            if self.manager.has_start_with_studio and now >= self._next_autostart_attempt:
                self._next_autostart_attempt = now + 5.0
                self.connect_service()

    def _controller_update(self, kind: str, payload: dict) -> None:
        if kind == "workers":
            self.manager.sync_mqtt(payload.get("workers", ()))
        self.update.emit(kind, payload)

    def _activate_managed_provider(self, family: str, model_id: str, worker_id: str) -> None:
        controller = self.controller
        if controller is None or family not in {"detection", "depth"}:
            return
        controller.select_provider("detector" if family == "detection" else "depth", model_id, worker_id)
        self.config = _controller_config()


def _controller_config(
    preferences: Settings | None = None,
    environment: Mapping[str, str] | None = None,
) -> ControllerConfig:
    preferences = preferences or settings()
    environment = os.environ if environment is None else environment

    def selected(environment_key: str, preference_key) -> str:
        configured = str(environment.get(environment_key) or "").strip()
        return configured or preferences.get(preference_key)

    robot_id = str(environment.get("DESK_BUDDY_ROBOT_ID") or "esp32_5")
    return ControllerConfig(
        robot_id=robot_id,
        robot_topic=str(environment.get("DESK_BUDDY_ROBOT_TOPIC") or robot_id),
        detector_model_id=selected("DESK_BUDDY_DETECTOR_MODEL", keys.VISION_DETECTOR_MODEL),
        depth_model_id=selected("DESK_BUDDY_DEPTH_MODEL", keys.VISION_DEPTH_MODEL),
        detector_worker_id=selected("DESK_BUDDY_DETECTOR_WORKER", keys.VISION_DETECTOR_WORKER),
        depth_worker_id=selected("DESK_BUDDY_DEPTH_WORKER", keys.VISION_DEPTH_WORKER),
        trainer_worker_id=str(environment.get("DESK_BUDDY_TRAINER_WORKER") or "ik-trainer-1"),
        inference_worker_id=str(environment.get("DESK_BUDDY_INFERENCE_WORKER") or "ik-inference-1"),
        topic_root=str(environment.get("DESK_BUDDY_VISION_TOPIC_ROOT") or "desk_buddy"),
    )


def _application_data_root() -> Path:
    return vision_data_root()


def _env_bool(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
