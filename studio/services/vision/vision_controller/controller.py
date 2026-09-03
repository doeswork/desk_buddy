"""Studio-owned coordinator for detector, depth, learned IK, and firmware MQTT."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from ..contracts import (
    DEPTH_SCHEMA,
    FEATURE_SCHEMA,
    OBSERVATION_SCHEMA,
    ContractError,
    DepthMapV1,
    DetectionBatchV1,
    DetectionV1,
    IKPredictionV1,
    IKTargetV1,
    JobEnvelope,
    JobEvent,
    VisionObservationV1,
    WorkerStatus,
)
from ..frames import BinaryArtifact, decode_artifact, encode_artifact
from ..ik_control import (
    DeterministicGridStrategy,
    IKControl,
    IKPlanningContext,
    LearnedModelCompatibility,
    calibrate,
    commands_for_target,
    target_from_prediction,
    validate_target,
)
from ..ik_control.legacy import FirmwarePhoto, decode_firmware_photo, legacy_progress, legacy_terminal
from ..model_builder import ModelBuilderClient, VisionStore
from ..topics import ModelRoute, VisionTopics, WorkerRegistry

COMPOSED_PIPELINE_DISABLED = "composed detector/depth/MLP pipelines are not enabled"


class ControllerTransport(Protocol):
    def subscribe(self, topic: str, *, qos: int = 1) -> None: ...

    def publish(self, topic: str, payload: bytes | str | Mapping[str, Any], *, qos: int, retain: bool = False) -> bool: ...


@dataclass(frozen=True)
class ControllerConfig:
    robot_id: str
    robot_topic: str
    detector_model_id: str
    depth_model_id: str
    detector_worker_id: str
    depth_worker_id: str
    trainer_worker_id: str = "ik-trainer-1"
    inference_worker_id: str = "ik-inference-1"
    topic_root: str = "desk_buddy"
    box_threshold: float = 0.25
    text_threshold: float = 0.2

    def routes(self, learned_model_ids: tuple[str, ...] = ()) -> tuple[ModelRoute, ...]:
        return (
            ModelRoute(self.detector_model_id, self.detector_worker_id, "zero_shot.infer"),
            ModelRoute(self.depth_model_id, self.depth_worker_id, "depth.infer"),
            ModelRoute("ik-mlp-builder", self.trainer_worker_id, "ik_model.train"),
            *(ModelRoute(model_id, self.inference_worker_id, "ik_model.infer") for model_id in learned_model_ids),
            *(ModelRoute(model_id, self.inference_worker_id, "ik_model.load") for model_id in learned_model_ids),
        )


@dataclass
class PipelineContext:
    capture_id: str
    request_id: str
    robot_id: str
    prompt: str
    jpeg: bytes
    execute: bool
    planner: str
    learned_model_id: str
    legacy_action_id: str = ""
    jobs: dict[str, str] = field(default_factory=dict)
    artifacts: dict[str, dict[str, BinaryArtifact]] = field(default_factory=dict)
    detection_batch: DetectionBatchV1 | None = None
    depth_map: DepthMapV1 | None = None
    observation: VisionObservationV1 | None = None
    deterministic_target: IKTargetV1 | None = None
    learned_target: IKTargetV1 | None = None
    selected_detection: DetectionV1 | None = None
    state: str = "processing"
    error: str = ""
    timings_ms: dict[str, float] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "capture_id": self.capture_id,
            "request_id": self.request_id,
            "robot_id": self.robot_id,
            "prompt": self.prompt,
            "execute": self.execute,
            "planner": self.planner,
            "learned_model_id": self.learned_model_id,
            "state": self.state,
            "error": self.error,
            "detection_batch": self.detection_batch.as_dict() if self.detection_batch else None,
            "depth_map": self.depth_map.as_dict() if self.depth_map else None,
            "observation": self.observation.as_dict() if self.observation else None,
            "selected_detection": self.selected_detection.as_dict() if self.selected_detection else None,
            "deterministic_target": self.deterministic_target.as_dict() if self.deterministic_target else None,
            "learned_target": self.learned_target.as_dict() if self.learned_target else None,
            "timings_ms": dict(self.timings_ms),
        }


class VisionController:
    """Coordinates MQTT workers; it is the only vision code that publishes motion."""

    def __init__(
        self,
        *,
        config: ControllerConfig,
        transport: ControllerTransport,
        store: VisionStore,
    ) -> None:
        self.config = config
        self.transport = transport
        self.store = store
        self.topics = VisionTopics(config.topic_root)
        learned = tuple(model["model_id"] for model in store.models(config.robot_id))
        self.registry = WorkerRegistry(config.routes(learned))
        self.detector_model_id = config.detector_model_id
        self.detector_worker_id = config.detector_worker_id
        self.depth_model_id = config.depth_model_id
        self.depth_worker_id = config.depth_worker_id
        self._contexts: dict[str, PipelineContext] = {}
        self._capture_by_job: dict[str, str] = {}
        self._job_expectations: dict[str, tuple[str, str, str]] = {}
        self._pending_legacy: dict[str, dict[str, Any]] = {}
        self._health_jobs: dict[str, str] = {}
        self._calibration_grids: dict[str, dict[str, Any]] = {}
        saved_calibration = store.calibration(config.robot_id)
        if saved_calibration is not None:
            self._calibration_grids[config.robot_id] = dict(saved_calibration["grid"])
        self._compatibility: dict[str, LearnedModelCompatibility] = {}
        self._active_model_id = next((model["model_id"] for model in store.models(config.robot_id) if model["active"]), "")
        self._lock = threading.RLock()
        self.on_update: Callable[[str, dict[str, Any]], None] | None = None
        self.ik = IKControl(self._publish_firmware, sender="visual_ai")
        self.model_builder = ModelBuilderClient(
            transport=transport,
            topics=self.topics,
            registry=self.registry,
            store=store,
            trainer_worker_id=config.trainer_worker_id,
            inference_worker_id=config.inference_worker_id,
        )
        self.model_builder.on_update = self._model_builder_update
        self._refresh_model_metadata()
        self.ik.on_dispatch = lambda dispatch: self._emit("execution", {"state": "dispatch", **asdict(dispatch)})
        self.ik.on_terminal = self._execution_terminal

    @property
    def firmware_topic(self) -> str:
        return f"{self.config.robot_topic}/test"

    def start(self) -> None:
        self.transport.subscribe(self.topics.all_status(), qos=1)
        self.transport.subscribe(self.topics.all_events(), qos=1)
        self.transport.subscribe(self.topics.all_artifacts_out(), qos=1)
        self.transport.subscribe(self.firmware_topic, qos=0)

    def set_calibration_grid(self, robot_id: str, grid: Mapping[str, Any]) -> None:
        self._calibration_grids[robot_id] = dict(grid)

    def select_provider(self, kind: str, model_id: str, worker_id: str) -> None:
        job_kind = {"detector": "zero_shot.infer", "depth": "depth.infer"}.get(kind)
        if job_kind is None:
            raise ValueError("provider kind must be detector or depth")
        status = self.registry.status(worker_id)
        if status is None or not status.supports(model_id, job_kind):
            raise ContractError("worker_unavailable", f"worker {worker_id} is not ready for {model_id}")
        self.registry.set_route(ModelRoute(model_id, worker_id, job_kind))
        if kind == "detector":
            self.detector_model_id, self.detector_worker_id = model_id, worker_id
        else:
            self.depth_model_id, self.depth_worker_id = model_id, worker_id
        self._emit("providers", {
            "detector_model_id": self.detector_model_id,
            "detector_worker_id": self.detector_worker_id,
            "depth_model_id": self.depth_model_id,
            "depth_worker_id": self.depth_worker_id,
        })

    def set_active_model(self, model_id: str) -> None:
        raise RuntimeError(COMPOSED_PIPELINE_DISABLED)

    def review_capture(
        self,
        capture_id: str,
        *,
        disposition: str,
        rotation_deg: float | None = None,
        distance_mm: float | None = None,
        z_height_mm: float | None = None,
        notes: str = "",
    ) -> None:
        self.store.review(
            capture_id,
            disposition=disposition,
            rotation_deg=rotation_deg,
            distance_mm=distance_mm,
            z_height_mm=z_height_mm,
            notes=notes,
        )
        self._emit("review", {"capture_id": capture_id, "disposition": disposition})

    def start_training(self, model_name: str, *, epochs: int | None = None) -> str:
        raise RuntimeError(COMPOSED_PIPELINE_DISABLED)

    def load_model(self, model_id: str) -> str:
        raise RuntimeError(COMPOSED_PIPELINE_DISABLED)

    def test_review_target(
        self, capture_id: str, *, rotation_deg: float, distance_mm: float, z_height_mm: float
    ) -> None:
        if capture_id not in self._contexts:
            raise ValueError(f"unknown capture {capture_id}")
        target = validate_target(
            IKTargetV1(rotation_deg, distance_mm, z_height_mm, "operator-review.v1"),
            self.ik.limits,
        )
        if not target.accepted:
            raise ValueError(", ".join(target.rejection_reasons))
        result = self.ik.execute(
            topic=self.firmware_topic,
            operation_id=f"review-{capture_id}",
            target=target,
            grab=False,
            telemetry=False,
        )
        if not hasattr(result, "payload"):
            raise RuntimeError(getattr(result, "error", None) or "could not start review target")

    def register_learned_model(self, compatibility: LearnedModelCompatibility) -> None:
        self._compatibility[compatibility.model_id] = compatibility
        self.registry.set_route(ModelRoute(compatibility.model_id, self.config.inference_worker_id, "ik_model.infer"))

    def process_image(
        self,
        jpeg: bytes,
        *,
        prompt: str,
        request_id: str | None = None,
        capture_id: str | None = None,
        execute: bool = False,
        planner: str = "deterministic",
        learned_model_id: str = "",
        legacy_action_id: str = "",
    ) -> str:
        if planner != "deterministic":
            message = COMPOSED_PIPELINE_DISABLED
            if legacy_action_id:
                self.transport.publish(
                    self.firmware_topic,
                    legacy_terminal(legacy_action_id, success=False, stage="failed", error=message),
                    qos=0,
                )
            raise ValueError(message)
        if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise ValueError("vision input must be a complete JPEG")
        if not prompt.strip():
            raise ValueError("an object prompt is required")
        capture_id = capture_id or uuid.uuid4().hex
        request_id = request_id or uuid.uuid4().hex
        original_id = f"{capture_id}-original"
        self.store.artifacts.put(original_id, jpeg, ".jpg")
        self.store.begin_capture(
            capture_id=capture_id,
            request_id=request_id,
            robot_id=self.config.robot_id,
            prompt=prompt,
            original_artifact_id=original_id,
        )
        context = PipelineContext(
            capture_id=capture_id,
            request_id=request_id,
            robot_id=self.config.robot_id,
            prompt=prompt.strip(),
            jpeg=bytes(jpeg),
            execute=bool(execute),
            planner=planner,
            learned_model_id=learned_model_id,
            legacy_action_id=legacy_action_id,
        )
        with self._lock:
            self._contexts[capture_id] = context
        try:
            self._submit_image_job(context, "zero_shot.infer", self.detector_model_id)
        except Exception as exc:
            self._fail(context, str(exc))
            raise
        self._emit("pipeline", context.snapshot())
        if legacy_action_id:
            self.transport.publish(
                self.firmware_topic,
                legacy_progress(legacy_action_id, "processing", "Zero-shot detection started"),
                qos=0,
            )
        return capture_id

    def context(self, capture_id: str) -> PipelineContext | None:
        return self._contexts.get(capture_id)

    def latest(self) -> PipelineContext | None:
        if not self._contexts:
            return None
        return self._contexts[next(reversed(self._contexts))]

    def expire_commands(self) -> None:
        self.ik.expire()

    def probe_worker(self, worker_id: str, model_id: str) -> str:
        """Request an MQTT round-trip health check without running inference."""
        status = self.registry.status(worker_id)
        if status is None or not status.ready:
            raise ContractError("worker_unavailable", f"worker {worker_id} is not ready")
        job_id = uuid.uuid4().hex
        job = JobEnvelope(
            job_id=job_id,
            request_id=uuid.uuid4().hex,
            capture_id=f"health-{job_id}",
            robot_id=self.config.robot_id,
            worker_id=worker_id,
            kind="job.status",
            model_id=model_id,
            payload={"probe": True},
        )
        self._health_jobs[job_id] = worker_id
        if not self.transport.publish(self.topics.request(worker_id), job.as_dict(), qos=1):
            self._health_jobs.pop(job_id, None)
            raise RuntimeError("could not publish worker health probe")
        self._emit("health", {"job_id": job_id, "worker_id": worker_id, "state": "submitted"})
        return job_id

    def handle_message(self, topic: str, payload: bytes) -> None:
        try:
            if topic.endswith("/status") and "/vision/worker/" in topic:
                self._handle_status(payload)
            elif topic.endswith("/event") and "/vision/worker/" in topic:
                self._handle_event(payload)
            elif topic.endswith("/artifact/out") and "/vision/worker/" in topic:
                self._handle_artifact(payload)
            elif topic == self.firmware_topic:
                self._handle_firmware(payload)
        except Exception as exc:
            if topic.endswith("/event"):
                try:
                    job_id = str(json.loads(payload.decode("utf-8")).get("job_id") or "")
                    capture_id = self._capture_by_job.get(job_id)
                    context = self._contexts.get(capture_id or "")
                    if context is not None:
                        self._fail(context, f"malformed worker result: {exc}")
                except Exception:
                    pass
            self._emit("error", {"message": str(exc), "topic": topic})

    def _handle_status(self, payload: bytes) -> None:
        raw = json.loads(payload.decode("utf-8"))
        status = WorkerStatus.from_dict(raw)
        self.registry.update(status)
        self._emit("workers", {"workers": [item.as_dict() for item in self.registry.statuses()]})

    def _handle_artifact(self, payload: bytes) -> None:
        artifact = decode_artifact(payload)
        if self.model_builder.handle_artifact(artifact):
            return
        capture_id = self._capture_by_job.get(artifact.job_id)
        if not capture_id:
            return
        context = self._contexts.get(capture_id)
        if context is None:
            return
        by_role = context.artifacts.setdefault(artifact.job_id, {})
        existing = by_role.get(artifact.role)
        if existing is not None and existing.sha256 != artifact.sha256:
            self._fail(context, f"conflicting artifact {artifact.role}")
            return
        by_role[artifact.role] = artifact
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "application/x-npz": ".npz", "application/x-pytorch": ".pt"}.get(artifact.mime_type, ".bin")
        self.store.artifacts.put(artifact.artifact_id, artifact.payload, suffix)
        self._maybe_finish_observation(context)

    def _handle_event(self, payload: bytes) -> None:
        event = JobEvent.from_dict(json.loads(payload.decode("utf-8")))
        health_worker = self._health_jobs.get(event.job_id)
        if health_worker is not None:
            self._emit("health", {
                "job_id": event.job_id,
                "worker_id": health_worker,
                "state": event.status,
                "payload": event.payload,
                "error": event.error or {},
            })
            if event.status in {"completed", "failed"}:
                self._health_jobs.pop(event.job_id, None)
            return
        if self.model_builder.handle_event(event):
            return
        capture_id = self._capture_by_job.get(event.job_id)
        if not capture_id:
            return
        context = self._contexts.get(capture_id)
        if context is None or event.status not in {"completed", "failed"}:
            return
        if event.capture_id != context.capture_id or event.request_id != context.request_id or event.robot_id != context.robot_id:
            self._fail(context, "worker response correlation mismatch")
            return
        expected = self._job_expectations.get(event.job_id)
        if expected != (event.kind, event.model_id, event.worker_id):
            self._fail(context, "worker response route mismatch")
            return
        if event.status == "failed":
            message = str((event.error or {}).get("message") or "worker job failed")
            self._fail(context, f"{event.kind}: {message}")
            return
        if "timing_ms" in event.payload:
            context.timings_ms[event.kind] = float(event.payload["timing_ms"])
        if event.kind == "zero_shot.infer":
            context.detection_batch = DetectionBatchV1.from_dict(event.payload["detection_batch"])
            if (
                context.detection_batch.model_id != event.model_id
                or context.detection_batch.model_version != event.model_version
            ):
                self._fail(context, "detector result model identity mismatch")
                return
            self.store.update_capture(context.capture_id, detection_json=context.detection_batch.as_dict())
            if context.planner == "deterministic":
                self._finish_deterministic(context)
                return
        elif event.kind == "depth.infer":
            context.depth_map = DepthMapV1.from_dict(event.payload["depth_map"])
            self.store.update_capture(context.capture_id, depth_json=context.depth_map.as_dict())
        elif event.kind == "ik_model.infer":
            self._finish_learned(context, IKPredictionV1.from_dict(event.payload["prediction"]))
            return
        self._maybe_finish_observation(context)

    def _finish_deterministic(self, context: PipelineContext) -> None:
        if context.detection_batch is None:
            return
        if not context.detection_batch.detections:
            self._fail(context, "zero-shot detector returned no matching objects")
            return
        detection = max(context.detection_batch.detections, key=lambda item: item.score)
        context.selected_detection = detection
        grid = self._calibration_grids.get(context.robot_id)
        if grid is None:
            context.deterministic_target = IKTargetV1(
                0, 0, 0, "nine-point-grid.v1", accepted=False,
                rejection_reasons=("calibration_missing",),
            )
        else:
            planning = IKPlanningContext(
                capture_id=context.capture_id,
                robot_id=context.robot_id,
                prompt=context.prompt,
                detection=detection,
                detector_model_id=context.detection_batch.model_id,
                detector_model_version=context.detection_batch.model_version,
                calibration_profile_id=str(grid.get("profile_id") or ""),
            )
            context.deterministic_target = DeterministicGridStrategy(grid).plan(planning)
        self.store.update_capture(
            context.capture_id,
            deterministic_target_json=context.deterministic_target.as_dict(),
        )
        self._finish_target(context, context.deterministic_target)

    def _maybe_finish_observation(self, context: PipelineContext) -> None:
        if context.observation is not None or context.detection_batch is None or context.depth_map is None:
            return
        depth_job = context.jobs.get("depth.infer", "")
        depth_artifacts = context.artifacts.get(depth_job, {})
        normalized_artifact = depth_artifacts.get("normalized_depth")
        preview_artifact = depth_artifacts.get("depth_preview")
        if normalized_artifact is None or preview_artifact is None:
            return
        if (
            normalized_artifact.artifact_id != context.depth_map.normalized_artifact_id
            or preview_artifact.artifact_id != context.depth_map.preview_artifact_id
        ):
            self._fail(context, "depth artifact IDs do not match DepthMapV1")
            return
        if not context.detection_batch.detections:
            self._fail(context, "zero-shot detector returned no matching objects")
            return
        detection = max(context.detection_batch.detections, key=lambda item: item.score)
        context.selected_detection = detection
        patch, statistics = _depth_features(normalized_artifact.payload, detection.bbox_norm)
        observation = VisionObservationV1(
            capture_id=context.capture_id,
            robot_id=context.robot_id,
            prompt=context.prompt,
            detection=detection,
            depth_patch_64x64=patch,
            depth_statistics=statistics,
            detector_model_id=context.detection_batch.model_id,
            detector_model_version=context.detection_batch.model_version,
            depth_model_id=context.depth_map.model_id,
            depth_model_version=context.depth_map.model_version,
            calibration_profile_id=str(self._calibration_grids.get(context.robot_id, {}).get("profile_id") or ""),
        )
        context.observation = observation
        grid = self._calibration_grids.get(context.robot_id)
        planning = IKPlanningContext(
            capture_id=context.capture_id,
            robot_id=context.robot_id,
            prompt=context.prompt,
            detection=detection,
            detector_model_id=context.detection_batch.model_id,
            detector_model_version=context.detection_batch.model_version,
            calibration_profile_id=str((grid or {}).get("profile_id") or ""),
            depth_observation=observation,
        )
        context.deterministic_target = (
            DeterministicGridStrategy(grid).plan(planning)
            if grid is not None
            else IKTargetV1(0, 0, 0, "nine-point-grid.v1", accepted=False, rejection_reasons=("calibration_missing",))
        )
        self.store.update_capture(
            context.capture_id,
            observation_json=observation.as_dict(),
            deterministic_target_json=context.deterministic_target.as_dict(),
        )
        if context.planner == "learned":
            if not context.learned_model_id:
                self._fail(context, "learned planner selected without a model")
                return
            self._submit_observation_job(context)
            return
        self._finish_target(context, context.deterministic_target)

    def _finish_learned(self, context: PipelineContext, prediction: IKPredictionV1) -> None:
        compatibility = self._compatibility.get(prediction.model_id)
        if compatibility is None or context.observation is None:
            self._fail(context, "learned model compatibility metadata is unavailable")
            return
        context.learned_target = target_from_prediction(context.observation, prediction, compatibility)
        if context.deterministic_target is None or not context.deterministic_target.accepted:
            context.learned_target = IKTargetV1(
                rotation_deg=context.learned_target.rotation_deg,
                distance_mm=context.learned_target.distance_mm,
                z_height_mm=context.learned_target.z_height_mm,
                strategy_id=context.learned_target.strategy_id,
                model_id=context.learned_target.model_id,
                accepted=False,
                rejection_reasons=tuple(dict.fromkeys((*context.learned_target.rejection_reasons, "calibration_coverage_missing"))),
            )
        self.store.update_capture(context.capture_id, learned_target_json=context.learned_target.as_dict())
        self._finish_target(context, context.learned_target)

    def _finish_target(self, context: PipelineContext, target: IKTargetV1) -> None:
        checked = validate_target(target, self.ik.limits)
        context.state = "ready" if checked.accepted else "rejected"
        self.store.update_capture(context.capture_id, status=context.state)
        self._emit("pipeline", context.snapshot())
        if not checked.accepted:
            self._fail(context, ", ".join(checked.rejection_reasons))
            return
        if not context.execute:
            context.state = "shadow" if context.planner == "learned" else "preview"
            self.store.update_capture(context.capture_id, status=context.state)
            self._complete_legacy(context, stage="detection_only")
            self._emit("pipeline", context.snapshot())
            return
        if context.planner == "learned" and context.learned_model_id != self._active_model_id:
            self._fail(context, "learned model is not active; shadow prediction only")
            return
        context.state = "executing"
        result = self.ik.execute(
            topic=self.firmware_topic,
            operation_id=context.capture_id,
            target=checked,
            grab=True,
            telemetry=True,
        )
        if not hasattr(result, "payload"):
            self._fail(context, getattr(result, "error", "could not start IK execution") or "could not start IK execution")

    def _submit_image_job(self, context: PipelineContext, kind: str, model_id: str) -> None:
        route = self.registry.route(model_id, kind)
        job_id = uuid.uuid4().hex
        job = JobEnvelope(
            job_id=job_id,
            request_id=context.request_id,
            capture_id=context.capture_id,
            robot_id=context.robot_id,
            worker_id=route.worker_id,
            kind=kind,
            model_id=model_id,
            payload={
                "prompt": context.prompt,
                "box_threshold": self.config.box_threshold,
                "text_threshold": self.config.text_threshold,
            },
            input_artifact_roles=("image",),
        )
        context.jobs[kind] = job_id
        self._capture_by_job[job_id] = context.capture_id
        self._job_expectations[job_id] = (kind, model_id, route.worker_id)
        frame = encode_artifact(
            job_id=job_id,
            artifact_id=f"{context.capture_id}-image",
            role="image",
            mime_type="image/jpeg",
            payload=context.jpeg,
            metadata={"schema": "image.jpeg.v1", "capture_id": context.capture_id},
        )
        if not self.transport.publish(self.topics.request(route.worker_id), job.as_dict(), qos=1):
            raise RuntimeError(f"could not publish {kind} request")
        if not self.transport.publish(self.topics.artifact_in(route.worker_id), frame, qos=1):
            raise RuntimeError(f"could not publish {kind} image")

    def _submit_observation_job(self, context: PipelineContext) -> None:
        route = self.registry.route(context.learned_model_id, "ik_model.infer")
        job_id = uuid.uuid4().hex
        job = JobEnvelope(
            job_id=job_id,
            request_id=context.request_id,
            capture_id=context.capture_id,
            robot_id=context.robot_id,
            worker_id=route.worker_id,
            kind="ik_model.infer",
            model_id=context.learned_model_id,
            payload={"observation": context.observation.as_dict()},
        )
        context.jobs["ik_model.infer"] = job_id
        self._capture_by_job[job_id] = context.capture_id
        self._job_expectations[job_id] = ("ik_model.infer", context.learned_model_id, route.worker_id)
        if not self.transport.publish(self.topics.request(route.worker_id), job.as_dict(), qos=1):
            self._fail(context, "could not publish learned IK request")

    def _handle_firmware(self, payload: bytes) -> None:
        photo = decode_firmware_photo(payload)
        if photo is None and payload.startswith(b"\xff\xd8") and payload.endswith(b"\xff\xd9") and self._pending_legacy:
            action_id = next(reversed(self._pending_legacy))
            pending = self._pending_legacy[action_id]
            photo = FirmwarePhoto({"action_id": action_id}, payload)
        if photo is not None:
            action_id = str(photo.metadata.get("action_id") or "")
            pending = self._pending_legacy.pop(action_id, {})
            action = str(photo.metadata.get("type") or photo.metadata.get("action") or pending.get("type") or pending.get("action") or "")
            if action == "calibrate_depth":
                self._process_calibration_photo(photo.jpeg, action_id)
            elif action == "detect_object":
                prompt = str(photo.metadata.get("phrase") or pending.get("phrase") or "object")
                self.process_image(
                    photo.jpeg,
                    prompt=prompt,
                    request_id=action_id or None,
                    execute=bool(pending.get("execute", pending.get("use_model", pending.get("useModel", False)))),
                    planner="learned" if pending.get("use_model", pending.get("useModel", False)) else "deterministic",
                    learned_model_id=str(pending.get("model_name") or self._active_model_id),
                    legacy_action_id=action_id,
                )
            return
        try:
            body = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(body, dict):
            return
        if str(body.get("sender") or "").lower() == "visual_ai":
            return
        if self.ik.handle_firmware(self.firmware_topic, body):
            return
        action_id = str(body.get("action_id") or "")
        kind = str(body.get("type") or body.get("action") or "")
        if action_id and kind in {"detect_object", "calibrate_depth"}:
            self._pending_legacy[action_id] = dict(body)

    def _process_calibration_photo(self, jpeg: bytes, action_id: str) -> None:
        try:
            result = calibrate(jpeg)
            profile_id = f"cal-{uuid.uuid4().hex[:12]}"
            grid = {**result.grid, "profile_id": profile_id}
            artifact_id = f"{profile_id}-annotated"
            self.store.artifacts.put(artifact_id, result.annotated_jpeg, ".jpg")
            self.store.save_calibration(
                robot_id=self.config.robot_id,
                profile_id=profile_id,
                points=result.points,
                grid=grid,
                annotated_artifact_id=artifact_id,
            )
            self.set_calibration_grid(self.config.robot_id, grid)
            self.transport.publish(
                self.firmware_topic,
                legacy_terminal(
                    action_id, success=True, stage="calibration_completed",
                    operation_type="calibrate_depth", calibration_points=result.points,
                    calibration_profile_id=profile_id,
                ),
                qos=0,
            )
            self._emit("calibration", {"state": "completed", "profile_id": profile_id, "points": result.points})
        except Exception as exc:
            self.transport.publish(
                self.firmware_topic,
                legacy_terminal(
                    action_id, success=False, stage="failed", operation_type="calibrate_depth", error=str(exc)
                ),
                qos=0,
            )
            self._emit("calibration", {"state": "failed", "error": str(exc)})

    def _publish_firmware(self, topic: str, payload: Mapping[str, Any], qos: int) -> bool:
        return self.transport.publish(topic, payload, qos=qos)

    def _execution_terminal(self, result) -> None:
        context = self._contexts.get(result.operation_id)
        if context is None:
            return
        if result.success:
            context.state = "completed"
            self.store.update_capture(context.capture_id, status="completed")
            self._complete_legacy(context, stage="reach_and_grab_completed", grab_status="completed")
        else:
            self._fail(context, result.error or "IK execution failed")
        self._emit("pipeline", context.snapshot())

    def _complete_legacy(self, context: PipelineContext, *, stage: str, **values: Any) -> None:
        if context.legacy_action_id:
            target = context.learned_target if context.planner == "learned" else context.deterministic_target
            compatibility_values: dict[str, Any] = {"capture_id": context.capture_id, "phrase": context.prompt}
            detection = context.selected_detection or (
                context.observation.detection if context.observation is not None else None
            )
            if detection is not None:
                center_x, center_y = detection.center_norm
                compatibility_values.update({
                    "raw_x": int(round(center_x * 100.0)),
                    "raw_y": int(round((1.0 - center_y) * 100.0)),
                    "detection": detection.as_dict(),
                })
            if target is not None and target.accepted:
                compatibility_values["picking_up_instructions"] = {
                    str(index): {"action": command.action, **command.body}
                    for index, command in enumerate(commands_for_target(target, grab=True, telemetry=True), 1)
                }
            self.transport.publish(
                self.firmware_topic,
                legacy_terminal(
                    context.legacy_action_id, success=True, stage=stage,
                    **compatibility_values, **values,
                ),
                qos=0,
            )

    def _fail(self, context: PipelineContext, message: str) -> None:
        if context.state == "failed":
            return
        context.state = "failed"
        context.error = message
        self.store.update_capture(context.capture_id, status="failed")
        if context.legacy_action_id:
            self.transport.publish(
                self.firmware_topic,
                legacy_terminal(context.legacy_action_id, success=False, stage="failed", error=message),
                qos=0,
            )
        self._emit("pipeline", context.snapshot())

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        if self.on_update:
            self.on_update(kind, payload)

    def _model_builder_update(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "training" and payload.get("state") == "completed":
            self._refresh_model_metadata()
        self._emit(kind, payload)

    def _refresh_model_metadata(self) -> None:
        for record in self.store.models(self.config.robot_id):
            metadata = record["metadata"]
            provider = dict(metadata.get("provider") or {})
            required = (
                "detector_model_id", "detector_model_version",
                "depth_model_id", "depth_model_version",
            )
            if all(provider.get(key) for key in required):
                self.register_learned_model(LearnedModelCompatibility(
                    model_id=record["model_id"],
                    detector_model_id=str(provider["detector_model_id"]),
                    detector_model_version=str(provider["detector_model_version"]),
                    depth_model_id=str(provider["depth_model_id"]),
                    depth_model_version=str(provider["depth_model_version"]),
                    feature_schema=str(metadata.get("feature_schema") or FEATURE_SCHEMA),
                ))


def _depth_features(payload: bytes, bbox_norm: tuple[float, float, float, float]) -> tuple[tuple[float, ...], dict[str, float]]:
    import numpy as np
    from PIL import Image

    with np.load(BytesIO(payload), allow_pickle=False) as values:
        depth = np.asarray(values["depth"], dtype=np.float32)
    if depth.ndim != 2 or depth.size == 0 or not np.all(np.isfinite(depth)):
        raise ValueError("normalized depth artifact must contain one finite 2D array")
    depth = np.clip(depth, 0.0, 1.0)
    height, width = depth.shape
    x0, y0, x1, y1 = bbox_norm
    left = min(width - 1, max(0, int(min(x0, x1) * width)))
    right = min(width, max(left + 1, int(max(x0, x1) * width + 0.999)))
    top = min(height - 1, max(0, int(min(y0, y1) * height)))
    bottom = min(height, max(top + 1, int(max(y0, y1) * height + 0.999)))
    crop = depth[top:bottom, left:right]
    resized = Image.fromarray(crop).resize((64, 64), Image.Resampling.BILINEAR)
    patch = np.clip(np.asarray(resized, dtype=np.float32), 0.0, 1.0).reshape(-1)
    stats = {
        "mean": float(np.mean(patch)),
        "median": float(np.median(patch)),
        "std": float(np.std(patch)),
        "min": float(np.min(patch)),
        "max": float(np.max(patch)),
        "p05": float(np.percentile(patch, 5)),
        "p95": float(np.percentile(patch, 95)),
    }
    return tuple(float(value) for value in patch), stats
