"""Vision page lifecycle, managed services, and MQTT orchestration."""

from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QMessageBox, QTabWidget, QWidget

from ....services.network import broker_commands
from ....services.vision.access import (
    VisionAccessIdentity,
    VisionAccessManager,
)
from ....services.vision.vision_controller import VisionService
from ...components import ActionSpec, Column, Separator, SidePanel
from ..base import Page
from .pipeline import PipelineCallbacks, capture_card, worker_controls_card
from .results import operation_card, preview_card
from .services import ServiceCallbacks, services_tabs
from .state import ProviderOption, VisionPageState, provider_options, selected_option
from .training import TrainingCallbacks, model_builder_card, review_card, trainer_runtime_card
from .widgets import EventSink
from .access_widgets import (
    AccessCallbacks,
    service_access_card,
    vision_access_overview,
)


class VisionPage(Page):
    key = "vision"
    label = "Vision"
    title = "Vision Services"
    subtitle = "Manage model workers, capture detections, and run reusable IK over MQTT."
    status = "Vision MQTT disconnected"
    built = True

    def __init__(self, *, vision_access: VisionAccessManager | None = None) -> None:
        super().__init__()
        self.service = VisionService(access=vision_access)
        self.state = VisionPageState(managed_services=self.service.manager.snapshot())
        self._sink = EventSink(self._service_update, self._connection_update)
        self.service.update.connect(self._sink.update)
        self.service.connection_changed.connect(self._sink.connection)
        self.service.access.changed.connect(self._vision_access_changed)

    def build_actions(self) -> list:
        if not self.service.connected:
            return [
                ActionSpec("Connect", primary=True, on_click=self.service.connect_service),
                Separator(), ActionSpec("Test Photo"), ActionSpec("Execute"),
            ]
        if not self._pipeline_ready():
            return [
                ActionSpec("Disconnect", on_click=self.service.disconnect_service),
                Separator(), ActionSpec("Test Photo"), ActionSpec("Execute"),
            ]
        return [
            ActionSpec("Disconnect", on_click=self.service.disconnect_service),
            Separator(),
            ActionSpec("Test Photo", primary=True, on_click=self._choose_preview),
            ActionSpec("Execute", on_click=self._choose_execute),
        ]

    def build_side(self) -> QWidget:
        items = []
        for family in ("detection", "depth", "mlp"):
            managed = self.state.managed_services.get(family, {})
            model = managed.get("active_model_id") or managed.get("candidate_model_id") or "none"
            process = managed.get("process_state", "stopped")
            mqtt = managed.get("mqtt_state", "offline")
            items.append(f"{family.title()} — {model} · {process}/{mqtt}")
        remote = [worker for worker in self.state.workers if not self._is_managed_worker(str(worker.get("worker_id") or ""))]
        items.extend(
            f"Remote — {worker.get('worker_id')} · {'ready' if worker.get('ready') else 'offline'}"
            for worker in remote
        )
        return SidePanel("Vision Services", items)

    def build_page(self) -> QWidget:
        detector_options, depth_options = self._provider_options()
        detector_candidate = self._candidate("detection", detector_options)
        depth_candidate = self._candidate("depth", depth_options)
        controller = self.service.controller
        robot_id = controller.config.robot_id if controller else self.service.config.robot_id
        models = self.service.store.models(robot_id)
        examples = len(self.service.store.training_examples(robot_id))
        manifests = {(item.model_id, item.worker_id): item for item in self.service.manager.catalog}
        access_plan = self.service.access.current_plan(self.service.manager.catalog)
        access_states = {item.identity_id: item for item in self.service.access.states(access_plan)}

        def worker_access(role: str, worker_id: str):
            identity = access_plan.worker(worker_id) or VisionAccessIdentity(
                f"worker:{worker_id}", role, worker_id,
            )
            return identity, access_states.get(identity.identity_id) or self.service.access.state(identity)

        controller_access = access_plan.identity("controller")
        assert controller_access is not None
        controller_state = access_states[controller_access.identity_id]
        detector_access = worker_access("Detection", detector_candidate[1])
        depth_access = worker_access("Depth", depth_candidate[1])
        mlp_worker = str(self.state.managed_services.get("mlp", {}).get("active_worker_id") or "ik-inference-1")
        trainer_worker = str(self.state.managed_services.get("trainer", {}).get("active_worker_id") or "ik-trainer-1")
        mlp_access = worker_access("Custom MLP inference", mlp_worker)
        trainer_access = worker_access("MLP trainer", trainer_worker)

        external_callbacks = PipelineCallbacks(
            start_worker=self._start_external,
            stop_worker=self._stop_external,
            install_worker=self._install_external,
        )
        service_callbacks = ServiceCallbacks(
            candidate_changed=self._candidate_changed,
            install=self._install_managed,
            use_start=self._use_and_start,
            start=self._start_managed,
            stop=self._stop_managed,
            restart=self._restart_managed,
            health=self._health_check,
            remove_download=self._remove_download,
            reset_runtime=self._reset_runtime,
            cancel_install=self.service.manager.cancel_install,
            autostart_changed=self.service.manager.set_start_with_studio,
            import_manifest=self._import_manifest,
        )
        access_callbacks = AccessCallbacks(
            setup_all=self._setup_all_access,
            ensure=self._ensure_access,
            rotate=self._rotate_access,
            revoke=self._revoke_access,
            copy_setup=self._copy_access_setup,
        )
        training_callbacks = TrainingCallbacks(
            test_pose=self._test_pose,
            review=self._review,
            train=lambda name: None,
            load=lambda: None,
            activate=lambda: None,
        )

        selected_detector, _ = self._selected_providers()
        active_detector = selected_option(detector_options, selected_detector)
        tabs = QTabWidget()
        tabs.setObjectName("VisionWorkspace")
        tabs.addTab(
            Column(
                vision_access_overview(
                    access_states.values(), access_callbacks,
                    setup_enabled=self.service.access.can_manage_broker,
                ),
                service_access_card(controller_access, controller_state, access_callbacks),
                services_tabs(
                detector_options=detector_options,
                depth_options=depth_options,
                detector_candidate=detector_candidate,
                depth_candidate=depth_candidate,
                managed_states=self.state.managed_services,
                manifests=manifests,
                learned_models=models,
                mlp_candidate=str(self.state.managed_services.get("mlp", {}).get("candidate_model_id") or ""),
                external_workers=worker_controls_card(self.service.launcher, external_callbacks),
                callbacks=service_callbacks,
                access_callbacks=access_callbacks,
                detector_access=detector_access,
                depth_access=depth_access,
                mlp_access=mlp_access,
                current_tab=self.state.service_tab,
                tab_changed=lambda index: setattr(self.state, "service_tab", index),
                ),
            ),
            "Services",
        )
        tabs.addTab(
            Column(capture_card(
                state=self.state,
                detector=active_detector,
                prompt_changed=lambda value: setattr(self.state, "prompt", value),
            )),
            "Capture",
        )
        tabs.addTab(
            Column(
                preview_card(controller, self.state.latest),
                operation_card(self.state.latest),
                review_card(self.state.latest, self.service.connected, training_callbacks),
            ),
            "Results",
        )
        tabs.addTab(
            Column(
                model_builder_card(
                    examples=examples,
                    models=models,
                    connected=self.service.connected,
                    selected_model_id="",
                    callbacks=training_callbacks,
                ),
                trainer_runtime_card(
                    self.state.managed_services.get("trainer", {}),
                    install=self._install_managed,
                    start=self._start_managed,
                    stop=self._stop_managed,
                    restart=self._restart_managed,
                    health=self._health_check,
                    reset=self._reset_runtime,
                    autostart=self.service.manager.set_start_with_studio,
                ),
                service_access_card(trainer_access[0], trainer_access[1], access_callbacks),
            ),
            "Model Builder",
        )
        tabs.setCurrentIndex(min(self.state.top_tab, tabs.count() - 1))
        tabs.currentChanged.connect(lambda index: setattr(self.state, "top_tab", index))
        return tabs

    def _selected_providers(self) -> tuple[tuple[str, str], tuple[str, str]]:
        controller = self.service.controller
        config = controller.config if controller is not None else self.service.config
        return (
            (
                controller.detector_model_id if controller else config.detector_model_id,
                controller.detector_worker_id if controller else config.detector_worker_id,
            ),
            (
                controller.depth_model_id if controller else config.depth_model_id,
                controller.depth_worker_id if controller else config.depth_worker_id,
            ),
        )

    def _provider_options(self) -> tuple[tuple[ProviderOption, ...], tuple[ProviderOption, ...]]:
        selected_detector, selected_depth = self._selected_providers()
        return (
            provider_options(
                "detector", self.state.workers, selected=selected_detector,
                manifests=self.service.manager.manifests("detection"),
            ),
            provider_options(
                "depth", self.state.workers, selected=selected_depth,
                manifests=self.service.manager.manifests("depth"),
            ),
        )

    def _candidate(self, family: str, options: tuple[ProviderOption, ...]) -> tuple[str, str]:
        explicit = self.state.service_candidates.get(family)
        if explicit and any(item.key == explicit for item in options):
            return explicit
        managed = self.state.managed_services.get(family, {})
        if managed.get("remote"):
            active = (
                str(managed.get("active_model_id") or ""),
                str(managed.get("active_worker_id") or ""),
            )
            if any(item.key == active for item in options):
                return active
        model_id = str(managed.get("candidate_model_id") or "")
        option = next((item for item in options if item.model_id == model_id), None)
        return option.key if option else (options[0].key if options else ("", ""))

    def _pipeline_ready(self) -> bool:
        detector_options, _ = self._provider_options()
        detector_key, _ = self._selected_providers()
        detector = selected_option(detector_options, detector_key)
        return bool(detector and detector.ready)

    def _candidate_changed(self, family: str, option: ProviderOption) -> None:
        if not option.model_id:
            return
        self.state.service_candidates[family] = option.key
        if family in {"detection", "depth"} and self.service.manager.manifest(option.model_id, family):
            self.service.manager.set_candidate(family, option.model_id)
        elif family == "mlp":
            self.service.manager.set_candidate("mlp", option.model_id)

    def _install_managed(self, family: str, repair: bool) -> None:
        try:
            self.service.manager.install_candidate(family, repair=repair)
        except Exception as exc:
            QMessageBox.warning(self.widget(), "Could not install vision service", str(exc))

    def _use_and_start(self, family: str, option: ProviderOption) -> None:
        if not self.service.connected:
            QMessageBox.information(self.widget(), "Connect Vision", "Connect Studio to MQTT before starting or selecting a provider.")
            return
        manifest = self.service.manager.manifest(option.model_id, family)
        try:
            if manifest is not None:
                self.service.manager.use_and_start(family)
            else:
                self._select_remote(family, option)
        except Exception as exc:
            QMessageBox.warning(self.widget(), "Could not switch provider", str(exc))

    def _select_remote(self, family: str, option: ProviderOption) -> None:
        controller = self.service._require_controller()
        role = "detector" if family == "detection" else "depth"
        controller.select_provider(role, option.model_id, option.worker_id)
        self.service.manager.set_external_active(family, option.model_id, option.worker_id)
        self.rebuild()

    def _start_managed(self, family: str) -> None:
        self._manager_action("Could not start service", lambda: self.service.manager.start(family))

    def _stop_managed(self, family: str) -> None:
        self._manager_action("Could not stop service", lambda: self.service.manager.stop(family))

    def _restart_managed(self, family: str) -> None:
        self._manager_action("Could not restart service", lambda: self.service.manager.restart(family))

    def _health_check(self, worker_id: str, model_id: str) -> None:
        try:
            self.service._require_controller().probe_worker(worker_id, model_id)
            self.status = f"Health check sent to {worker_id}"
            self.rebuild()
        except Exception as exc:
            QMessageBox.warning(self.widget(), "Health check failed", str(exc))

    def _remove_download(self, family: str) -> None:
        if QMessageBox.question(
            self.widget(), "Remove model download",
            "Remove this managed model snapshot? Captures and trained checkpoints are not affected.",
        ) == QMessageBox.Yes:
            self._manager_action("Could not remove model", lambda: self.service.manager.remove_candidate_download(family))

    def _reset_runtime(self, family: str) -> None:
        if QMessageBox.question(
            self.widget(), "Reset worker runtime",
            "Remove this managed virtual environment? Downloaded model snapshots and captures are preserved.",
        ) == QMessageBox.Yes:
            self._manager_action("Could not reset runtime", lambda: self.service.manager.reset_runtime(family))

    def _import_manifest(self, family: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self.widget(), "Import provider manifest", "", "TOML (*.toml)")
        if not path:
            return
        try:
            manifest = self.service.manager.import_manifest(path)
            if manifest.family != family:
                QMessageBox.information(self.widget(), "Manifest imported", f"Added to the {manifest.family.title()} tab.")
        except Exception as exc:
            QMessageBox.warning(self.widget(), "Could not import manifest", str(exc))

    def _manager_action(self, title: str, callback) -> None:
        try:
            callback()
        except Exception as exc:
            QMessageBox.warning(self.widget(), title, str(exc))

    def _setup_all_access(self) -> None:
        if not self.service.access.can_manage_broker:
            QMessageBox.information(
                self.widget(), "External MQTT broker",
                "Studio only provisions its own broker at 127.0.0.1:18830. "
                "Supply external broker credentials through environment variables.",
            )
            return
        if not broker_commands.is_ours():
            result = broker_commands.start()
            if not result:
                QMessageBox.warning(self.widget(), result.message, result.detail)
                return
        result = self.service.access.ensure_all(
            self.service.access.current_plan(self.service.manager.catalog)
        )
        if not result.ok:
            QMessageBox.warning(
                self.widget(), "Some Vision accounts need attention", "\n".join(result.errors)
            )

    def _ensure_access(self, identity: VisionAccessIdentity) -> None:
        self._access_action(
            "Could not create Vision access", lambda: self.service.access.ensure(identity)
        )

    def _rotate_access(self, identity: VisionAccessIdentity) -> None:
        if QMessageBox.question(
            self.widget(), f"Rotate {identity.role} access?",
            "The current password will stop working immediately. Continue?",
        ) == QMessageBox.Yes:
            self._access_action(
                "Could not rotate Vision access", lambda: self.service.access.rotate(identity)
            )

    def _revoke_access(self, identity: VisionAccessIdentity) -> None:
        if QMessageBox.question(
            self.widget(), f"Revoke {identity.role} access?",
            "This service will disconnect and cannot reconnect until access is created again.",
        ) == QMessageBox.Yes:
            self._access_action(
                "Could not revoke Vision access", lambda: self.service.access.revoke(identity)
            )

    def _copy_access_setup(self, identity: VisionAccessIdentity) -> None:
        from PySide6.QtGui import QGuiApplication

        try:
            QGuiApplication.clipboard().setText(self.service.access.setup_bundle(identity))
            self.status = f"Copied MQTT setup for {identity.role}"
            self.rebuild()
        except Exception as exc:
            QMessageBox.warning(self.widget(), "Could not copy Vision setup", str(exc))

    def _access_action(self, title: str, callback) -> None:
        try:
            callback()
        except Exception as exc:
            QMessageBox.warning(self.widget(), title, str(exc))

    def _vision_access_changed(self) -> None:
        self._side = None
        self.rebuild()

    def _choose_preview(self) -> None:
        self._choose_and_submit(False)

    def _choose_execute(self) -> None:
        if QMessageBox.question(
            self.widget(), "Execute physical movement",
            "This will command the selected robot after detection and safety validation. Continue?",
        ) == QMessageBox.Yes:
            self._choose_and_submit(True)

    def _choose_and_submit(self, execute: bool) -> None:
        if not self._pipeline_ready():
            QMessageBox.warning(self.widget(), "Detector unavailable", "The selected detector is not MQTT-ready.")
            return
        path, _ = QFileDialog.getOpenFileName(self.widget(), "Choose an ESP32 photo", "", "Images (*.jpg *.jpeg)")
        if not path:
            return
        try:
            self.service.process_file(
                path, prompt=self.state.prompt.strip(), execute=execute,
                planner="deterministic", learned_model_id="",
            )
        except Exception as exc:
            QMessageBox.warning(self.widget(), "Vision request failed", str(exc))

    def _review(self, disposition: str, rotation: float, distance: float, height: float) -> None:
        controller = self.service.controller
        if controller is None or not self.state.latest.get("capture_id"):
            return
        try:
            controller.review_capture(
                self.state.latest["capture_id"], disposition=disposition,
                rotation_deg=rotation if disposition == "successful" else None,
                distance_mm=distance if disposition == "successful" else None,
                z_height_mm=height if disposition == "successful" else None,
            )
        except Exception as exc:
            QMessageBox.warning(self.widget(), "Could not save review", str(exc))

    def _test_pose(self, rotation: float, distance: float, height: float) -> None:
        if QMessageBox.question(
            self.widget(), "Test supervised pose",
            "This sends the adjusted rotation and IK pose to the robot without grabbing. Continue?",
        ) != QMessageBox.Yes:
            return
        try:
            self.service._require_controller().test_review_target(
                self.state.latest["capture_id"], rotation_deg=rotation,
                distance_mm=distance, z_height_mm=height,
            )
        except Exception as exc:
            QMessageBox.warning(self.widget(), "Could not test pose", str(exc))

    def _start_external(self, worker_id: str) -> None:
        self._manager_action("Could not start external worker", lambda: self.service.launcher.start(worker_id))

    def _stop_external(self, worker_id: str) -> None:
        self._manager_action("Could not stop external worker", lambda: self.service.launcher.stop(worker_id))

    def _install_external(self, worker_id: str) -> None:
        self._manager_action("Could not install external worker", lambda: self.service.launcher.install(worker_id))

    def _is_managed_worker(self, worker_id: str) -> bool:
        return any(item.worker_id == worker_id for item in self.service.manager.catalog) or worker_id in {"ik-inference-1", "ik-trainer-1"}

    def _connection_update(self, connected: bool, message: str) -> None:
        self.status = message
        if not connected:
            self.state.workers = []
            self._side = None
        self.rebuild()

    def _service_update(self, kind: str, payload: dict) -> None:
        if kind == "workers":
            self.state.workers = list(payload.get("workers", []))
            self._side = None
        elif kind == "managed_services":
            self.state.managed_services = payload
            self._side = None
        elif kind == "pipeline":
            self.state.latest = payload
            self.status = f"Vision: {payload.get('state', 'processing')}"
        elif kind == "health":
            self.state.health = payload
            self.status = f"Health {payload.get('state', 'pending')}: {payload.get('worker_id', '')}"
        elif kind == "error":
            self.status = str(payload.get("message") or "Vision error")
        self.rebuild()
