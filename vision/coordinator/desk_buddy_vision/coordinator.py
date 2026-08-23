from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Mapping

import numpy as np

from desk_buddy_vision_protocol import (
    ContractError,
    Detection,
    JobEnvelope,
    JobResult,
    PlanCorrections,
    ServiceCapability,
    TopicLayout,
    VisionEvent,
    VisionRequest,
    decode_binary_frame,
    encode_binary_frame,
)
from desk_buddy_vision_protocol.artifacts import iter_chunks
from desk_buddy_vision_protocol.envelopes import JOB_SCHEMA, VISION_SCHEMA, decode_json_object, utc_now

from .artifacts import ArtifactStore, ArtifactUploadManager
from .calibration import calibrate, project_detection
from .config import AppConfig, ConfigError, ModelRoute
from .features import annotate_detection, build_feature_set, select_detection
from .firmware import firmware_photo_command, parse_firmware_payload
from .mqtt import MQTTTransport
from .planning import CalibrationProjection, MotionPlan, MotionTarget, SafetyLimits, build_motion_plan
from .sequencer import RobotCommandSequencer, RobotDispatch, SequenceTerminal, SequenceTransition
from .storage import OperationRecord, VisionStorage

LOGGER = logging.getLogger(__name__)


@dataclass
class _OperationContext:
    request: VisionRequest
    operation: OperationRecord
    image_artifact_id: str | None = None
    detector_job_id: str | None = None
    depth_job_id: str | None = None
    planner_job_id: str | None = None
    training_job_id: str | None = None
    activation_job_id: str | None = None
    detector_result: dict[str, Any] | None = None
    depth_result: dict[str, Any] | None = None
    feature_set: dict[str, Any] | None = None
    projection: CalibrationProjection | None = None
    baseline_plan: MotionPlan | None = None
    artifacts: dict[str, str] = field(default_factory=dict)


@dataclass
class _PendingPhoto:
    operation_id: str
    robot_id: str
    action_id: str
    deadline: float


class VisionCoordinator:
    """MQTT-only coordinator and authoritative owner of robot operations."""

    def __init__(self, *, config: AppConfig, transport: MQTTTransport) -> None:
        self.config = config
        self.transport = transport
        self.topics = TopicLayout(config.mqtt.topic_root)
        data_dir = config.coordinator.data_dir
        self.storage = VisionStorage(data_dir / "vision.sqlite3")
        self.artifacts = ArtifactStore(data_dir / "artifacts", self.storage)
        self.uploads = ArtifactUploadManager(self.artifacts)
        self.sequencer = RobotCommandSequencer(timeout_seconds=config.coordinator.command_timeout_seconds)
        self._limits = SafetyLimits(
            max_rotation_deg=config.coordinator.max_rotation_deg,
            min_distance_mm=config.coordinator.min_distance_mm,
            max_distance_mm=config.coordinator.max_distance_mm,
            min_z_height_mm=config.coordinator.min_z_height_mm,
            max_z_height_mm=config.coordinator.max_z_height_mm,
            max_rotation_correction_deg=config.coordinator.max_rotation_correction_deg,
            max_distance_correction_mm=config.coordinator.max_distance_correction_mm,
            max_z_correction_mm=config.coordinator.max_z_correction_mm,
            allow_extrapolated_motion=config.coordinator.allow_extrapolated_motion,
        )
        self._messages: queue.Queue[tuple[str, bytes] | None] = queue.Queue(maxsize=1024)
        self._thread = threading.Thread(target=self._run, name="vision-coordinator", daemon=True)
        self._stopping = threading.Event()
        self._contexts: dict[str, _OperationContext] = {}
        self._operation_by_job: dict[str, str] = {}
        self._pending_photos: dict[str, _PendingPhoto] = {}
        self._pending_photo_by_topic: dict[str, str] = {}
        self._active_robot_operations: dict[str, str] = {}
        self._job_deadlines: dict[str, float] = {}
        self._capabilities: dict[str, ServiceCapability] = {}

    def start(self) -> None:
        self.storage.recover_incomplete_operations()
        self.artifacts.cleanup_orphan_temporary_files()
        root = self.topics.root
        self.transport.subscribe(f"{root}/+/vision/request", qos=1)
        self.transport.subscribe(f"{root}/+/vision/artifact/request", qos=1)
        self.transport.subscribe(f"{root}/vision/service/+/event", qos=1)
        self.transport.subscribe(f"{root}/vision/service/+/status", qos=1)
        self.transport.subscribe(self.topics.artifact_request(), qos=1)
        self.transport.subscribe(f"{root}/vision/artifact/upload/+", qos=1)
        for robot in self.config.robots:
            self.transport.subscribe(robot.command_topic, qos=0)
            self.transport.subscribe(robot.heartbeat_topic, qos=0)
        self._thread.start()
        self.publish_status(online=True)

    def stop(self) -> None:
        self._stopping.set()
        self._messages.put(None)
        self._thread.join(timeout=10)
        self.publish_status(online=False)
        self.storage.close()

    def handle_message(self, topic: str, payload: bytes) -> None:
        """MQTT callback: copy and enqueue only; all work happens on the coordinator thread."""
        try:
            self._messages.put_nowait((str(topic), bytes(payload)))
        except queue.Full:
            LOGGER.error("coordinator queue full; dropping non-physical message on %s", topic)

    def publish_status(self, *, online: bool) -> None:
        payload = {
            "schema": VISION_SCHEMA,
            "sender": "vision_coordinator",
            "service_id": self.config.coordinator.service_id,
            "status": "online" if online else "offline",
            "updated_at": utc_now(),
        }
        self.transport.publish(self.topics.coordinator_status(), payload, qos=1, retain=True)
        for robot in self.config.robots:
            self.transport.publish(self.topics.vision_status(robot.robot_id), payload, qos=1, retain=True)

    def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                message = self._messages.get(timeout=0.5)
            except queue.Empty:
                self._tick()
                continue
            if message is None:
                return
            topic, payload = message
            try:
                self._process(topic, payload)
            except Exception:
                LOGGER.exception("failed to process MQTT message on %s", topic)
            self._tick()

    def _process(self, topic: str, payload: bytes) -> None:
        if topic.endswith("/vision/request"):
            self._handle_gui_request(topic, payload)
            return
        if "/vision/service/" in topic and topic.endswith("/status"):
            capability = ServiceCapability.from_mapping(decode_json_object(payload))
            topic_service_id = topic.split("/")[-2]
            if capability.service_id != topic_service_id:
                raise ContractError("service_topic_mismatch", "status service_id does not match MQTT topic")
            self._capabilities[capability.service_id] = capability
            return
        if "/vision/service/" in topic and topic.endswith("/event"):
            self._handle_job_event(payload, topic_service_id=topic.split("/")[-2])
            return
        if topic == self.topics.artifact_request():
            self._handle_worker_artifact_request(payload)
            return
        if "/vision/artifact/upload/" in topic:
            self._handle_worker_artifact_chunk(topic, payload)
            return
        robot = self._robot_for_topic(topic)
        if robot and topic == robot.command_topic:
            self._handle_firmware_message(robot.robot_id, topic, payload)

    def _handle_gui_request(self, topic: str, payload: bytes) -> None:
        robot_from_topic = topic.split("/")[-3]
        try:
            request = VisionRequest.from_json(payload)
            if request.robot_id != robot_from_topic:
                raise ContractError("robot_topic_mismatch", "robot_id does not match request topic")
            self.config.robot(request.robot_id)
        except (ContractError, ConfigError) as exc:
            request_id = "unknown"
            try:
                request_id = str(decode_json_object(payload).get("request_id") or "unknown")
            except ContractError:
                pass
            error = exc.as_dict() if isinstance(exc, ContractError) else {"code": "unknown_robot", "message": str(exc)}
            event = VisionEvent(
                request_id=request_id,
                operation_id="",
                status="failed",
                stage="request_validation",
                error=error,
            )
            self.transport.publish(self.topics.vision_event(robot_from_topic), event.as_dict(), qos=1)
            return

        operation, created = self.storage.create_operation(request)
        if not created:
            recovered = dict(operation.result or {})
            recovered["duplicate"] = True
            self._emit_record(operation, payload=recovered)
            return
        context = _OperationContext(request=request, operation=operation)
        self._contexts[operation.id] = context
        self._emit(context, status="accepted", stage="request_validation")
        try:
            if request.kind in {"photo", "detect", "calibration"}:
                self._request_photo(context)
            elif request.kind == "artifact_get":
                self._send_gui_artifact(context)
            elif request.kind == "model_list":
                self._complete(context, payload=self._model_listing())
            elif request.kind == "status":
                self._complete(context, payload=self._status_payload(context))
            elif request.kind == "training_example_update":
                self._update_training_example(context)
            elif request.kind == "training_start":
                self._start_training(context)
            elif request.kind == "model_activate":
                self._start_model_activation(context)
            elif request.kind == "operation_cancel":
                self._cancel_operation(context)
            else:
                self._fail(context, "not_implemented", f"request kind is not implemented: {request.kind}")
        except Exception as exc:
            LOGGER.exception("request %s failed", request.request_id)
            self._fail(context, "request_failed", str(exc))

    def _request_photo(self, context: _OperationContext) -> None:
        robot = self.config.robot(context.request.robot_id)
        if context.request.kind == "detect" and not self._validate_detect_services(context):
            return
        active_operation = self._active_robot_operations.get(context.request.robot_id)
        if (
            active_operation not in {None, context.operation.id}
            or robot.command_topic in self._pending_photo_by_topic
            or self.sequencer.is_busy(robot.command_topic)
        ):
            self._fail(context, "robot_busy", "the robot already has an active operation")
            return
        self._active_robot_operations[context.request.robot_id] = context.operation.id
        action = {"photo": "photo", "detect": "detect_object", "calibration": "calibrate_depth"}[context.request.kind]
        action_id = f"vision-{context.operation.id[:12]}"
        fields: dict[str, Any] = {}
        if context.request.kind == "detect":
            fields["phrase"] = str(context.request.payload.get("phrase") or "")
            if not fields["phrase"]:
                self._fail(context, "invalid_detect_request", "phrase is required")
                return
            if context.request.payload.get("magnet_position") is not None:
                fields["MagnetPosition"] = int(context.request.payload["magnet_position"])
        command = firmware_photo_command(action=action, action_id=action_id, **fields)
        pending = _PendingPhoto(
            operation_id=context.operation.id,
            robot_id=context.request.robot_id,
            action_id=action_id,
            deadline=time.monotonic() + self.config.coordinator.worker_timeout_seconds,
        )
        self._pending_photos[action_id] = pending
        self._pending_photo_by_topic[robot.command_topic] = action_id
        context.operation = self.storage.update_operation(
            context.operation.id, status="waiting_for_photo", stage="firmware_photo"
        )
        self._emit(context, status="waiting_for_photo", stage="firmware_photo")
        if not self.transport.publish(robot.command_topic, command, qos=0):
            self._clear_pending_photo(action_id, robot.command_topic)
            self._fail(context, "photo_publish_failed", "firmware photo command publish failed")

    def _validate_detect_services(self, context: _OperationContext) -> bool:
        for kind, field in (("detector", "detector_model"), ("depth", "depth_model")):
            selected = context.request.payload.get(field)
            try:
                route = self.config.route(str(selected), kind=kind) if selected else self.config.default_model(kind)
            except ConfigError as exc:
                self._fail(context, "unknown_model", str(exc))
                return False
            capability = self._capabilities.get(route.service_id)
            if capability is None or not capability.supports(route.model_id):
                self._fail(
                    context,
                    "unavailable_model",
                    f"{route.model_id} is not ready on configured service {route.service_id}",
                )
                return False
        return True

    def _handle_firmware_message(self, robot_id: str, topic: str, payload: bytes) -> None:
        try:
            parsed = parse_firmware_payload(payload)
        except ValueError:
            return
        if parsed.is_visual_ai_echo:
            return
        if parsed.body:
            transition = self.sequencer.handle_firmware_message(topic, parsed.body)
            if transition.consumed:
                action_id = str(parsed.body.get("action_id") or "")
                self.storage.finish_command(action_id, status=str(parsed.body.get("status")), response=parsed.body)
                self._apply_sequence_transition(transition)
                return
        if parsed.jpeg is None:
            return
        action_id = parsed.action_id or self._pending_photo_by_topic.get(topic)
        pending = self._pending_photos.get(str(action_id)) if action_id else None
        if pending is None or pending.robot_id != robot_id:
            LOGGER.warning("received uncorrelated JPEG on %s", topic)
            return
        context = self._contexts.get(pending.operation_id)
        if context is None:
            return
        self._clear_pending_photo(pending.action_id, topic)
        context.operation = self.storage.update_operation(
            context.operation.id, status="processing", stage="image_storage"
        )
        artifact = self.artifacts.save_bytes(
            operation_id=context.operation.id,
            robot_id=robot_id,
            kind="original",
            payload=parsed.jpeg,
            mime_type="image/jpeg",
            metadata={"firmware_action_id": pending.action_id, "request_kind": context.request.kind},
        )
        context.image_artifact_id = artifact.artifact_id
        context.artifacts["original"] = artifact.artifact_id
        self._emit(
            context,
            status="processing",
            stage="image_storage",
            payload={"artifact_id": artifact.artifact_id},
        )
        if context.request.kind == "photo":
            self._complete(context, payload={"artifacts": dict(context.artifacts)})
        elif context.request.kind == "calibration":
            self._process_calibration(context, parsed.jpeg)
        else:
            self._start_inference(context)

    def _process_calibration(self, context: _OperationContext, jpeg: bytes) -> None:
        result = calibrate(
            jpeg,
            reference_y_fraction=float(context.request.payload.get("reference_y_fraction", 0.1)),
        )
        annotated = self.artifacts.save_bytes(
            operation_id=context.operation.id,
            robot_id=context.request.robot_id,
            kind="calibration_annotated",
            payload=result.annotated_jpeg,
            mime_type="image/jpeg",
        )
        context.artifacts["calibration_annotated"] = annotated.artifact_id
        profile_id = self.storage.save_calibration(
            robot_id=context.request.robot_id,
            image_artifact_id=str(context.image_artifact_id),
            annotated_artifact_id=annotated.artifact_id,
            points=result.points,
            grid=result.grid,
            reference_y_fraction=float(context.request.payload.get("reference_y_fraction", 0.1)),
        )
        self._complete(
            context,
            payload={"profile_id": profile_id, "points": result.points, "artifacts": dict(context.artifacts)},
        )

    def _start_inference(self, context: _OperationContext) -> None:
        detector = self._selected_route(context, "detector", "detector_model")
        depth = self._selected_route(context, "depth", "depth_model")
        if detector is None or depth is None:
            return
        common = (str(context.image_artifact_id),)
        context.detector_job_id = self._dispatch_job(
            context,
            route=detector,
            kind="detector.infer",
            artifact_ids=common,
            payload={
                "phrase": context.request.payload["phrase"],
                "box_threshold": float(context.request.payload.get("box_threshold", 0.25)),
                "text_threshold": float(context.request.payload.get("text_threshold", 0.2)),
            },
        )
        context.depth_job_id = self._dispatch_job(
            context,
            route=depth,
            kind="depth.infer",
            artifact_ids=common,
            payload={"save_preview": bool(context.request.payload.get("save_artifacts", True))},
        )
        if context.detector_job_id is None or context.depth_job_id is None:
            return
        self._emit(context, status="processing", stage="model_inference")

    def _selected_route(self, context: _OperationContext, kind: str, field: str) -> ModelRoute | None:
        selected = context.request.payload.get(field)
        try:
            route = self.config.route(str(selected), kind=kind) if selected else self.config.default_model(kind)
        except ConfigError as exc:
            self._fail(context, "unknown_model", str(exc))
            return None
        capability = self._capabilities.get(route.service_id)
        if capability is None or not capability.supports(route.model_id):
            self._fail(
                context,
                "unavailable_model",
                f"{route.model_id} is not ready on configured service {route.service_id}",
            )
            return None
        return route

    def _dispatch_job(
        self,
        context: _OperationContext,
        *,
        route: ModelRoute,
        kind: str,
        artifact_ids: tuple[str, ...],
        payload: Mapping[str, Any],
    ) -> str | None:
        job = JobEnvelope(
            request_id=context.request.request_id,
            operation_id=context.operation.id,
            job_id=str(uuid.uuid4()),
            service_id=route.service_id,
            kind=kind,
            model_id=route.model_id,
            input_artifact_ids=artifact_ids,
            payload=dict(payload),
        )
        self.storage.create_service_job(job.as_dict())
        self._operation_by_job[job.job_id] = context.operation.id
        self._job_deadlines[job.job_id] = time.monotonic() + self.config.coordinator.worker_timeout_seconds
        if not self.transport.publish(self.topics.service_request(route.service_id), job.as_dict(), qos=1):
            self.storage.finish_service_job(
                job.job_id,
                {"status": "failed", "error": {"code": "job_publish_failed", "message": "MQTT publish failed"}},
            )
            self._fail(context, "job_publish_failed", f"failed to dispatch {kind}")
            return None
        self.storage.mark_job_published(job.job_id)
        return job.job_id

    def _handle_job_event(self, payload: bytes, *, topic_service_id: str) -> None:
        result = JobResult.from_json(payload)
        if result.service_id != topic_service_id:
            raise ContractError("service_topic_mismatch", "result service_id does not match MQTT topic")
        operation_id = self._operation_by_job.get(result.job_id)
        if operation_id is None:
            stored = self.storage.get_service_job(result.job_id)
            operation_id = str(stored["operation_id"]) if stored else None
        context = self._contexts.get(operation_id or "")
        if context is None:
            return
        if context.operation.status in {"completed", "failed", "cancelled"}:
            return
        job = self.storage.get_service_job(result.job_id)
        if not job or job["service_id"] != result.service_id or job["model_id"] != result.model_id:
            return
        expected = job["request"]
        if (
            result.request_id != job["request_id"]
            or result.operation_id != job["operation_id"]
            or result.kind != job["kind"]
            or result.service_id != expected["service_id"]
        ):
            self._job_deadlines.pop(result.job_id, None)
            self.storage.finish_service_job(
                result.job_id,
                {
                    "status": "failed",
                    "error": {
                        "code": "worker_result_mismatch",
                        "message": "worker result correlation fields do not match the directed job",
                    },
                },
            )
            self._fail(
                context,
                "worker_result_mismatch",
                "worker result correlation fields do not match the directed job",
            )
            return
        if result.status in {"accepted", "processing"}:
            self._emit(
                context,
                status="processing",
                stage="model_inference" if result.kind.endswith(".infer") else result.kind,
                payload={"job_id": result.job_id, "job_status": result.status},
            )
            return
        if not self.storage.finish_service_job(result.job_id, result.as_dict()):
            return
        self._job_deadlines.pop(result.job_id, None)
        if result.status != "completed":
            if result.kind == "residual.predict" and bool(context.request.payload.get("allow_fallback", True)):
                self._finalize_plan(context, corrections=None, planner_model_id=None, fallback_error=result.error)
            else:
                error = result.error or {"code": "worker_job_failed", "message": f"{result.kind} failed"}
                self._fail(context, str(error.get("code")), str(error.get("message")))
            return
        if result.kind == "detector.infer":
            context.detector_result = result.payload
            self._maybe_finish_inference(context)
        elif result.kind == "depth.infer":
            context.depth_result = result.payload
            context.artifacts.update({str(k): str(v) for k, v in result.payload.get("artifacts", {}).items()})
            self._maybe_finish_inference(context)
        elif result.kind == "residual.predict":
            corrections = PlanCorrections.from_mapping(result.payload.get("corrections") or {})
            self._finalize_plan(context, corrections=corrections, planner_model_id=result.model_id)
        elif result.kind == "training.start":
            self._finish_training(context, result)
        elif result.kind == "model.activate":
            self._finish_activation(context, result)

    def _maybe_finish_inference(self, context: _OperationContext) -> None:
        if context.detector_result is None or context.depth_result is None:
            return
        batch = context.detector_result.get("detection_batch")
        if not isinstance(batch, dict):
            self._fail(context, "invalid_detector_result", "detector did not return detection_batch")
            return
        detections = tuple(Detection.from_mapping(value) for value in batch.get("detections", []))
        selected = select_detection(detections)
        if selected is None:
            self.storage.save_observation(
                operation_id=context.operation.id,
                image_artifact_id=str(context.image_artifact_id),
                selected_detection=None,
                detections=[],
                depth_artifact_id=context.artifacts.get("raw_depth"),
            )
            self._complete(context, payload={"detections": [], "artifacts": dict(context.artifacts), "target_found": False})
            return
        if bool(context.request.payload.get("save_artifacts", True)):
            _, original = self.artifacts.read_bytes(str(context.image_artifact_id))
            annotation = self.artifacts.save_bytes(
                operation_id=context.operation.id,
                robot_id=context.request.robot_id,
                kind="detection_annotated",
                payload=annotate_detection(original, selected),
                mime_type="image/jpeg",
                model_id=selected.source_model_id,
                model_version=selected.source_model_version,
            )
            context.artifacts["detection_annotated"] = annotation.artifact_id

        planner = str(context.request.payload.get("planner", "deterministic"))
        if planner == "disabled":
            self.storage.save_observation(
                operation_id=context.operation.id,
                image_artifact_id=str(context.image_artifact_id),
                selected_detection=selected.as_dict(),
                detections=[item.as_dict() for item in detections],
                depth_artifact_id=context.artifacts.get("raw_depth"),
            )
            self._complete(
                context,
                payload={"target_found": True, "selected_detection": selected.as_dict(), "artifacts": dict(context.artifacts)},
            )
            return
        profile = self.storage.active_calibration(context.request.robot_id)
        if profile is None:
            self._fail(context, "calibration_required", "no active nine-point calibration profile")
            return
        projection = project_detection(
            selected.bbox_px,
            profile["grid"],
            reference_y_fraction=float(profile["reference_y_fraction"]),
            allow_extrapolation=self.config.coordinator.allow_extrapolated_motion,
        )
        context.projection = projection
        baseline = MotionTarget(projection.angle_deg, projection.distance_mm, projection.z_height_mm)
        normalized_id = context.artifacts.get("normalized_depth")
        if not normalized_id:
            self._fail(context, "invalid_depth_result", "normalized depth artifact is missing")
            return
        _, depth_payload = self.artifacts.read_bytes(normalized_id)
        normalized_depth = np.load(BytesIO(depth_payload), allow_pickle=False)
        depth_metadata = context.depth_result.get("depth", {})
        if not isinstance(depth_metadata, dict):
            self._fail(context, "invalid_depth_result", "depth metadata must be an object")
            return
        near_is_high = depth_metadata.get("near_is_high")
        if not isinstance(near_is_high, bool):
            self._fail(context, "invalid_depth_result", "depth result must declare near_is_high")
            return
        feature_set = build_feature_set(
            image_width=int(batch["image_width"]),
            image_height=int(batch["image_height"]),
            phrase=str(batch["phrase"]),
            detection=selected,
            normalized_depth=normalized_depth,
            depth_near_is_high=near_is_high,
            projection=projection,
            baseline=baseline,
        )
        context.feature_set = feature_set.as_dict()
        feature_snapshot = self.artifacts.save_bytes(
            operation_id=context.operation.id,
            robot_id=context.request.robot_id,
            kind="features",
            payload=json.dumps({"features": context.feature_set}, separators=(",", ":"), sort_keys=True).encode("utf-8"),
            mime_type="application/json",
            metadata={"feature_schema_version": "features.v1"},
        )
        context.artifacts["features"] = feature_snapshot.artifact_id
        self.storage.upsert_training_example(
            operation_id=context.operation.id,
            feature_snapshot_artifact_id=feature_snapshot.artifact_id,
        )
        self.storage.save_observation(
            operation_id=context.operation.id,
            image_artifact_id=str(context.image_artifact_id),
            selected_detection=selected.as_dict(),
            detections=[item.as_dict() for item in detections],
            depth_artifact_id=context.artifacts.get("raw_depth"),
            feature_set=context.feature_set,
            calibration_projection={
                "angle_deg": projection.angle_deg,
                "distance_mm": projection.distance_mm,
                "z_height_mm": projection.z_height_mm,
                "zone": projection.zone,
                "calibrated": projection.calibrated,
                "extrapolated": projection.extrapolated,
            },
        )
        if planner.startswith("residual:"):
            model_id = planner.split(":", 1)[1]
            try:
                route = self.config.route(model_id, kind="planner")
            except ConfigError as exc:
                if bool(context.request.payload.get("allow_fallback", True)):
                    self._finalize_plan(context, corrections=None, planner_model_id=None, fallback_error={"message": str(exc)})
                else:
                    self._fail(context, "unknown_model", str(exc))
                return
            capability = self._capabilities.get(route.service_id)
            if capability is None or not capability.supports(route.model_id, schema="features.v1"):
                if bool(context.request.payload.get("allow_fallback", True)):
                    self._finalize_plan(
                        context,
                        corrections=None,
                        planner_model_id=None,
                        fallback_error={"code": "unavailable_model", "message": model_id},
                    )
                else:
                    self._fail(context, "unavailable_model", model_id)
                return
            context.planner_job_id = self._dispatch_job(
                context,
                route=route,
                kind="residual.predict",
                artifact_ids=(),
                payload={"features": context.feature_set, "baseline": {"rotation_deg": baseline.rotation_deg, "distance_mm": baseline.distance_mm, "z_height_mm": baseline.z_height_mm}},
            )
            self._emit(context, status="planning", stage="residual_prediction")
        else:
            self._finalize_plan(context, corrections=None, planner_model_id=None)

    def _finalize_plan(
        self,
        context: _OperationContext,
        *,
        corrections: PlanCorrections | None,
        planner_model_id: str | None,
        fallback_error: Mapping[str, Any] | None = None,
    ) -> None:
        if context.projection is None:
            self._fail(context, "planning_failed", "calibration projection is missing")
            return
        if context.operation.status == "processing":
            context.operation = self.storage.update_operation(
                context.operation.id, status="planning", stage="motion_planning"
            )
        plan = build_motion_plan(
            operation_id=context.operation.id,
            projection=context.projection,
            limits=self._limits,
            corrections=corrections,
            planner_model_id=planner_model_id,
        )
        context.baseline_plan = plan
        self.storage.save_motion_plan(plan.as_dict())
        if not plan.safety.safe:
            self._fail(context, "unsafe_motion_plan", ", ".join(plan.safety.reasons))
            return
        payload: dict[str, Any] = {"motion_plan": plan.as_dict(), "artifacts": dict(context.artifacts)}
        if fallback_error:
            payload["planner_fallback"] = dict(fallback_error)
        if not bool(context.request.payload.get("execute", True)):
            self._complete(context, payload=payload)
            return
        context.operation = self.storage.update_operation(
            context.operation.id, status="executing", stage="motion_execution"
        )
        transition = self.sequencer.start(
            topic=self.config.robot(context.request.robot_id).command_topic,
            operation_id=context.operation.id,
            commands=plan.commands,
        )
        self._emit(context, status="executing", stage="motion_execution", payload=payload)
        self._apply_sequence_transition(transition)

    def _apply_sequence_transition(self, transition: SequenceTransition) -> None:
        if transition.dispatch:
            dispatch = transition.dispatch
            context = self._contexts.get(dispatch.operation_id)
            if context is None or context.baseline_plan is None:
                return
            self.storage.register_command(
                motion_plan_id=context.baseline_plan.plan_id,
                step_index=dispatch.step_index,
                action_id=dispatch.command_action_id,
                action=str(dispatch.payload["action"]),
                request=dispatch.payload,
            )
            if not self.transport.publish(dispatch.topic, dispatch.payload, qos=0):
                self._apply_sequence_transition(self.sequencer.mark_publish_failed(dispatch))
            return
        if transition.terminal:
            terminal = transition.terminal
            context = self._contexts.get(terminal.operation_id)
            if context is None:
                return
            payload = {
                "motion_plan": context.baseline_plan.as_dict() if context.baseline_plan else None,
                "command_history": list(terminal.events),
                "warning": terminal.warning,
                "artifacts": dict(context.artifacts),
            }
            if terminal.success:
                self._complete(context, payload=payload)
            else:
                self._fail(context, terminal.error or "motion_failed", "robot command sequence failed", payload=payload)

    def _handle_worker_artifact_request(self, payload: bytes) -> None:
        body = decode_json_object(payload)
        kind = str(body.get("kind") or "")
        if kind == "artifact.download.request":
            self._send_worker_artifact(body)
        elif kind == "artifact.upload.begin":
            self._begin_worker_upload(body)

    def _authorized_job(self, body: Mapping[str, Any]) -> dict[str, Any]:
        job = self.storage.get_service_job(str(body.get("job_id") or ""))
        if not job or job["service_id"] != str(body.get("service_id") or ""):
            raise PermissionError("artifact transfer is not associated with this service job")
        if job["operation_id"] != str(body.get("operation_id") or ""):
            raise PermissionError("artifact transfer operation does not match service job")
        if job["request_id"] != str(body.get("request_id") or ""):
            raise PermissionError("artifact transfer request does not match service job")
        if job["status"] not in {"accepted", "processing"}:
            raise PermissionError(f"service job is terminal: {job['status']}")
        return job

    def _send_worker_artifact(self, body: Mapping[str, Any]) -> None:
        service_id = str(body.get("service_id") or "")
        transfer_id = str(body.get("transfer_id") or "")
        try:
            job = self._authorized_job(body)
            artifact_id = str(body.get("artifact_id") or "")
            if artifact_id not in job["request"].get("input_artifact_ids", []):
                raise PermissionError("artifact is not an authorized job input")
            record, payload = self.artifacts.read_bytes(artifact_id)
            chunks = list(iter_chunks(payload, chunk_size=self.config.coordinator.artifact_chunk_size))
            metadata = {
                "schema": JOB_SCHEMA,
                "kind": "artifact.download.metadata",
                "service_id": service_id,
                "operation_id": job["operation_id"],
                "job_id": job["job_id"],
                "transfer_id": transfer_id,
                "artifact_id": artifact_id,
                "artifact_kind": record.kind,
                "mime_type": record.mime_type,
                "byte_size": record.byte_size,
                "sha256": record.sha256,
                "chunk_size": self.config.coordinator.artifact_chunk_size,
                "chunk_count": len(chunks),
                "width": record.width,
                "height": record.height,
                "dtype": record.dtype,
                "metadata": record.metadata,
            }
            self.transport.publish(self.topics.artifact_metadata(service_id), metadata, qos=1)
            for index, count, chunk in chunks:
                header = {
                    "schema": JOB_SCHEMA,
                    "kind": "artifact.download.chunk",
                    "service_id": service_id,
                    "operation_id": job["operation_id"],
                    "job_id": job["job_id"],
                    "transfer_id": transfer_id,
                    "artifact_id": artifact_id,
                    "chunk_index": index,
                    "chunk_count": count,
                }
                self.transport.publish(
                    self.topics.artifact_download(service_id), encode_binary_frame(header, chunk), qos=1
                )
        except Exception as exc:
            self.transport.publish(
                self.topics.artifact_metadata(service_id),
                {
                    "schema": JOB_SCHEMA,
                    "kind": "artifact.download.rejected",
                    "service_id": service_id,
                    "transfer_id": transfer_id,
                    "error": {"code": "artifact_download_rejected", "message": str(exc)},
                },
                qos=1,
            )

    def _begin_worker_upload(self, body: Mapping[str, Any]) -> None:
        service_id = str(body.get("service_id") or "")
        transfer_id = str(body.get("transfer_id") or "")
        try:
            job = self._authorized_job(body)
            operation = self.storage.get_operation(job["operation_id"])
            if operation is None:
                raise KeyError("operation does not exist")
            metadata = dict(body)
            metadata["kind"] = str(body["artifact_kind"])
            metadata["robot_id"] = operation.robot_id
            self.uploads.begin(metadata)
            self.transport.publish(
                self.topics.artifact_metadata(service_id),
                {
                    "schema": JOB_SCHEMA,
                    "kind": "artifact.upload.accepted",
                    "service_id": service_id,
                    "operation_id": operation.id,
                    "job_id": job["job_id"],
                    "transfer_id": transfer_id,
                    "artifact_id": metadata["artifact_id"],
                },
                qos=1,
            )
        except Exception as exc:
            self.transport.publish(
                self.topics.artifact_metadata(service_id),
                {
                    "schema": JOB_SCHEMA,
                    "kind": "artifact.upload.rejected",
                    "service_id": service_id,
                    "transfer_id": transfer_id,
                    "error": {"code": "artifact_upload_rejected", "message": str(exc)},
                },
                qos=1,
            )

    def _handle_worker_artifact_chunk(self, topic: str, payload: bytes) -> None:
        service_id = topic.rsplit("/", 1)[-1]
        frame = decode_binary_frame(payload)
        if frame.header.get("service_id") != service_id:
            return
        transfer_id = str(frame.header.get("transfer_id") or "")
        try:
            self.uploads.add_chunk(
                transfer_id,
                int(frame.header["chunk_index"]),
                frame.payload,
                service_id=service_id,
                job_id=str(frame.header.get("job_id") or ""),
                operation_id=str(frame.header.get("operation_id") or ""),
                artifact_id=str(frame.header.get("artifact_id") or ""),
            )
            if not self.uploads.is_complete(transfer_id):
                return
            record = self.uploads.finish(transfer_id)
            context = self._contexts.get(record.operation_id)
            if context:
                context.artifacts[record.kind] = record.artifact_id
            self.transport.publish(
                self.topics.artifact_metadata(service_id),
                {
                    "schema": JOB_SCHEMA,
                    "kind": "artifact.upload.completed",
                    "service_id": service_id,
                    "operation_id": record.operation_id,
                    "job_id": frame.header["job_id"],
                    "transfer_id": transfer_id,
                    "artifact_id": record.artifact_id,
                },
                qos=1,
            )
        except Exception as exc:
            self.uploads.abort(transfer_id)
            self.transport.publish(
                self.topics.artifact_metadata(service_id),
                {
                    "schema": JOB_SCHEMA,
                    "kind": "artifact.upload.rejected",
                    "service_id": service_id,
                    "transfer_id": transfer_id,
                    "error": {"code": "artifact_upload_failed", "message": str(exc)},
                },
                qos=1,
            )

    def _send_gui_artifact(self, context: _OperationContext) -> None:
        artifact_id = str(context.request.payload.get("artifact_id") or "")
        record, payload = self.artifacts.read_bytes(artifact_id)
        if record.robot_id != context.request.robot_id:
            raise PermissionError("artifact belongs to another robot")
        chunks = list(iter_chunks(payload, chunk_size=self.config.coordinator.artifact_chunk_size))
        self._emit(
            context,
            status="processing",
            stage="artifact_transfer",
            payload={
                "kind": "artifact.metadata",
                "artifact_id": record.artifact_id,
                "mime_type": record.mime_type,
                "byte_size": record.byte_size,
                "sha256": record.sha256,
                "chunk_size": self.config.coordinator.artifact_chunk_size,
                "chunk_count": len(chunks),
            },
        )
        for index, count, chunk in chunks:
            header = {
                "schema": VISION_SCHEMA,
                "kind": "artifact.chunk",
                "request_id": context.request.request_id,
                "operation_id": context.operation.id,
                "artifact_id": record.artifact_id,
                "chunk_index": index,
                "chunk_count": count,
            }
            self.transport.publish(
                self.topics.vision_artifact_chunk(context.request.robot_id),
                encode_binary_frame(header, chunk),
                qos=1,
            )
        self._complete(context, payload={"artifact_id": artifact_id, "chunks_sent": len(chunks)})

    def _update_training_example(self, context: _OperationContext) -> None:
        payload = context.request.payload
        operation_id = str(payload.get("operation_id") or "")
        observation = self.storage.get_observation(operation_id)
        if observation is None or observation.get("feature_set") is None:
            raise ValueError("operation has no feature set")
        feature_artifact_id = None
        with_feature = self.storage.training_examples(reviewed_only=False)
        for row in with_feature:
            if row["operation_id"] == operation_id:
                feature_artifact_id = row["feature_snapshot_artifact_id"]
                break
        targets = payload.get("targets")
        if not isinstance(targets, dict):
            raise ValueError("targets object is required")
        self.storage.upsert_training_example(
            operation_id=operation_id,
            feature_snapshot_artifact_id=feature_artifact_id,
            success=payload.get("success"),
            reviewed=bool(payload.get("reviewed", True)),
            eligible=bool(payload.get("eligible", True)),
            target_rotation_delta_deg=float(targets["rotation_delta_deg"]),
            target_distance_delta_mm=float(targets["distance_delta_mm"]),
            target_z_delta_mm=float(targets["z_height_delta_mm"]),
            notes=str(payload.get("notes") or ""),
        )
        self._complete(context, payload={"training_operation_id": operation_id, "updated": True})

    def _start_training(self, context: _OperationContext) -> None:
        payload = context.request.payload
        filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
        examples = self.storage.training_examples(
            robot_id=str(filters.get("robot_id") or context.request.robot_id),
            reviewed_only=bool(filters.get("reviewed_only", True)),
            success_only=bool(filters.get("success_only", False)),
        )
        manifest_examples = []
        input_artifacts: list[str] = []
        for row in examples:
            artifact_id = row.get("feature_snapshot_artifact_id")
            if not artifact_id:
                continue
            record, original = self.artifacts.read_bytes(str(artifact_id))
            value = json.loads(original.decode("utf-8"))
            value["targets"] = {
                "rotation_delta_deg": row["target_rotation_delta_deg"],
                "distance_delta_mm": row["target_distance_delta_mm"],
                "z_height_delta_mm": row["target_z_delta_mm"],
            }
            reviewed = self.artifacts.save_bytes(
                operation_id=context.operation.id,
                robot_id=context.request.robot_id,
                kind="training_feature_snapshot",
                payload=json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"),
                mime_type="application/json",
                metadata={"source_operation_id": row["operation_id"], "source_artifact_id": record.artifact_id},
            )
            input_artifacts.append(reviewed.artifact_id)
            manifest_examples.append(
                {"operation_id": row["operation_id"], "feature_artifact_id": reviewed.artifact_id}
            )
        if len(manifest_examples) < 3:
            raise ValueError("at least three eligible reviewed examples are required")
        manifest = {
            "schema": "training-manifest.v1",
            "feature_schema_version": str(payload.get("feature_schema_version", "features.v1")),
            "examples": manifest_examples,
            "seed": int(payload.get("seed", 42)),
            "validation_split": float(payload.get("validation_split", 0.2)),
            "created_at": utc_now(),
        }
        manifest_artifact = self.artifacts.save_bytes(
            operation_id=context.operation.id,
            robot_id=context.request.robot_id,
            kind="training_manifest",
            payload=json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("utf-8"),
            mime_type="application/json",
        )
        context.artifacts["training_manifest"] = manifest_artifact.artifact_id
        route_id = str(payload.get("planner_model") or "")
        route = self.config.route(route_id, kind="planner") if route_id else self.config.default_model("planner")
        capability = self._capabilities.get(route.service_id)
        if capability is None or not capability.supports(route.model_id, schema="training-manifest.v1"):
            raise ValueError(f"planner/training service unavailable: {route.service_id}")
        all_inputs = (manifest_artifact.artifact_id, *input_artifacts)
        context.training_job_id = self._dispatch_job(
            context,
            route=route,
            kind="training.start",
            artifact_ids=all_inputs,
            payload={
                "manifest_artifact_id": manifest_artifact.artifact_id,
                "planner_model_name": str(payload.get("planner_model_name") or route.model_id),
                "seed": int(payload.get("seed", 42)),
                "validation_split": float(payload.get("validation_split", 0.2)),
                "regularization": float(payload.get("regularization", 1.0)),
                "safety_bounds": {
                    "rotation_delta_deg": self._limits.max_rotation_correction_deg,
                    "distance_delta_mm": self._limits.max_distance_correction_mm,
                    "z_height_delta_mm": self._limits.max_z_correction_mm,
                },
            },
        )
        self._emit(context, status="processing", stage="training")

    def _finish_training(self, context: _OperationContext, result: JobResult) -> None:
        artifact_ids = result.payload.get("artifacts") if isinstance(result.payload.get("artifacts"), dict) else {}
        metadata = {
            "artifact_ids": dict(artifact_ids),
            "metrics": result.payload.get("metrics"),
            "training_manifest_artifact_id": context.artifacts.get("training_manifest"),
        }
        version = str(result.payload["trained_model_version"])
        self.storage.register_model(
            model_id=str(result.payload["trained_model_id"]),
            kind="planner",
            version=version,
            artifact_id=artifact_ids.get("model_weights"),
            service_id=result.service_id,
            feature_schema_version="features.v1",
            metadata=metadata,
        )
        context.artifacts.update({str(key): str(value) for key, value in artifact_ids.items()})
        self._complete(
            context,
            payload={
                "model_id": result.payload["trained_model_id"],
                "model_version": version,
                "metrics": result.payload.get("metrics"),
                "activation_required": True,
                "artifacts": dict(context.artifacts),
            },
        )

    def _start_model_activation(self, context: _OperationContext) -> None:
        model_id = str(context.request.payload.get("model_id") or "")
        version = str(context.request.payload.get("version") or "")
        model = self.storage.get_model(model_id, version)
        if model is None:
            raise ValueError(f"unknown model version: {model_id}:{version}")
        artifact_ids = model["metadata"].get("artifact_ids", {})
        weights = str(artifact_ids.get("model_weights") or "")
        metadata = str(artifact_ids.get("model_metadata") or "")
        if not weights or not metadata:
            raise ValueError("model bundle is incomplete")
        route = self.config.route(model_id, kind="planner")
        capability = self._capabilities.get(route.service_id)
        if capability is None or not capability.supports(route.model_id):
            raise ValueError(f"planner service unavailable: {route.service_id}")
        context.activation_job_id = self._dispatch_job(
            context,
            route=route,
            kind="model.activate",
            artifact_ids=(weights, metadata),
            payload={"weights_artifact_id": weights, "metadata_artifact_id": metadata, "version": version},
        )
        self._emit(context, status="processing", stage="model_activation")

    def _finish_activation(self, context: _OperationContext, result: JobResult) -> None:
        model_id = str(result.payload["model_id"])
        version = str(result.payload["model_version"])
        self.storage.activate_model(model_id, version)
        self._complete(context, payload={"model_id": model_id, "model_version": version, "active": True})

    def _cancel_operation(self, context: _OperationContext) -> None:
        target_id = str(context.request.payload.get("operation_id") or "")
        target = self.storage.get_operation(target_id)
        if target is None:
            raise ValueError("target operation does not exist")
        if target.status == "executing":
            raise ValueError("executing physical motion cannot be cancelled safely")
        if target.status in {"completed", "failed", "cancelled"}:
            raise ValueError(f"target operation is already terminal: {target.status}")
        target_context = self._contexts.get(target_id)
        self.storage.cancel_service_jobs(target_id)
        self.storage.update_operation(target_id, status="cancelled", stage="cancelled")
        if target_context:
            if self._active_robot_operations.get(target.robot_id) == target_id:
                self._active_robot_operations.pop(target.robot_id, None)
            self._emit(target_context, status="cancelled", stage="cancelled")
        self._complete(context, payload={"cancelled_operation_id": target_id})

    def _model_listing(self) -> dict[str, Any]:
        configured = [
            {
                "model_id": route.model_id,
                "kind": route.kind,
                "service_id": route.service_id,
                "default": route.default,
                "available": bool(
                    self._capabilities.get(route.service_id)
                    and self._capabilities[route.service_id].supports(route.model_id)
                ),
            }
            for route in self.config.models
        ]
        return {"configured_models": configured, "trained_models": self.storage.list_models()}

    def _status_payload(self, context: _OperationContext) -> dict[str, Any]:
        requested_operation_id = str(context.request.payload.get("operation_id") or "")
        requested_request_id = str(context.request.payload.get("request_id") or "")
        operation = None
        if requested_operation_id:
            operation = self.storage.get_operation(requested_operation_id)
        elif requested_request_id:
            operation = self.storage.get_operation_by_request(requested_request_id)
        if operation is not None and operation.robot_id != context.request.robot_id:
            operation = None

        result: dict[str, Any] = {
            "coordinator": "online",
            "services": {service_id: capability.as_dict() for service_id, capability in self._capabilities.items()},
            "robots": [robot.robot_id for robot in self.config.robots],
        }
        if requested_operation_id or requested_request_id:
            result["operation"] = self._operation_payload(operation) if operation else None
        else:
            limit = int(context.request.payload.get("limit", 20))
            result["recent_operations"] = [
                self._operation_payload(item)
                for item in self.storage.list_operations(robot_id=context.request.robot_id, limit=limit)
                if item.id != context.operation.id
            ]
        return result

    @staticmethod
    def _operation_payload(operation: OperationRecord) -> dict[str, Any]:
        return {
            "operation_id": operation.id,
            "request_id": operation.request_id,
            "robot_id": operation.robot_id,
            "kind": operation.kind,
            "status": operation.status,
            "stage": operation.stage,
            "result": dict(operation.result or {}),
            "error": (
                {"code": operation.error_code, "message": operation.error_message}
                if operation.error_code
                else None
            ),
            "created_at": operation.created_at,
            "updated_at": operation.updated_at,
        }

    def _tick(self) -> None:
        now = time.monotonic()
        for transfer_id in self.uploads.expire(
            max_age_seconds=self.config.coordinator.worker_timeout_seconds,
            now=now,
        ):
            LOGGER.warning("expired incomplete worker artifact upload %s", transfer_id)
        for transition in self.sequencer.expire(now=now):
            self._apply_sequence_transition(transition)
        for action_id, pending in list(self._pending_photos.items()):
            if pending.deadline > now:
                continue
            robot = self.config.robot(pending.robot_id)
            self._clear_pending_photo(action_id, robot.command_topic)
            context = self._contexts.get(pending.operation_id)
            if context:
                self._fail(context, "photo_timeout", "firmware photo timed out; command was not retried")
        for job_id, deadline in list(self._job_deadlines.items()):
            if deadline > now:
                continue
            self._job_deadlines.pop(job_id, None)
            operation_id = self._operation_by_job.get(job_id)
            context = self._contexts.get(operation_id or "")
            job = self.storage.get_service_job(job_id)
            if context and job:
                self.storage.finish_service_job(
                    job_id,
                    {"status": "failed", "error": {"code": "worker_timeout", "message": "worker job timed out"}},
                )
                if job["kind"] == "residual.predict" and bool(context.request.payload.get("allow_fallback", True)):
                    self._finalize_plan(
                        context,
                        corrections=None,
                        planner_model_id=None,
                        fallback_error={"code": "worker_timeout", "message": "residual planner timed out"},
                    )
                else:
                    self._fail(context, "worker_timeout", f"{job['kind']} timed out")

    def _clear_pending_photo(self, action_id: str, topic: str) -> None:
        self._pending_photos.pop(action_id, None)
        if self._pending_photo_by_topic.get(topic) == action_id:
            self._pending_photo_by_topic.pop(topic, None)

    def _robot_for_topic(self, topic: str):
        for robot in self.config.robots:
            if topic in {robot.command_topic, robot.heartbeat_topic}:
                return robot
        return None

    def _emit(
        self,
        context: _OperationContext,
        *,
        status: str,
        stage: str,
        payload: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> None:
        event = VisionEvent(
            request_id=context.request.request_id,
            operation_id=context.operation.id,
            status=status,
            stage=stage,
            payload=dict(payload or {}),
            error=dict(error) if error else None,
        )
        self.transport.publish(self.topics.vision_event(context.request.robot_id), event.as_dict(), qos=1)

    def _emit_record(self, operation: OperationRecord, *, payload: Mapping[str, Any] | None = None) -> None:
        event = VisionEvent(
            request_id=operation.request_id,
            operation_id=operation.id,
            status=operation.status,
            stage=operation.stage,
            payload=dict(payload or {}),
            error={"code": operation.error_code, "message": operation.error_message} if operation.error_code else None,
        )
        self.transport.publish(self.topics.vision_event(operation.robot_id), event.as_dict(), qos=1)

    def _complete(self, context: _OperationContext, *, payload: Mapping[str, Any]) -> None:
        if context.operation.status not in {"completed", "failed", "cancelled"}:
            context.operation = self.storage.update_operation(
                context.operation.id,
                status="completed",
                stage="completed",
                result=payload,
            )
        if self._active_robot_operations.get(context.request.robot_id) == context.operation.id:
            self._active_robot_operations.pop(context.request.robot_id, None)
        self._emit(context, status="completed", stage="completed", payload=payload)

    def _fail(
        self,
        context: _OperationContext,
        code: str,
        message: str,
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        if context.operation.status in {"completed", "failed", "cancelled"}:
            return
        context.operation = self.storage.update_operation(
            context.operation.id,
            status="failed",
            stage="failed",
            error_code=code,
            error_message=message,
            result=payload,
        )
        if self._active_robot_operations.get(context.request.robot_id) == context.operation.id:
            self._active_robot_operations.pop(context.request.robot_id, None)
        self._emit(
            context,
            status="failed",
            stage="failed",
            payload=payload,
            error={"code": code, "message": message},
        )
