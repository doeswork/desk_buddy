"""Standalone MQTT service for one loaded zero-shot detector."""

from __future__ import annotations

import json
import math
import os
import queue
import signal
import threading
import time
from dataclasses import dataclass
from collections import OrderedDict
from pathlib import Path

from .calibration import ALGORITHM, CalibrationError, calibrate, project
from .detector import HuggingFaceDetector
from .frames import BinaryFrame, decode_frame, decode_metadata, validate_photo_frame
from .motion import CommandSequencer, MotionError
from .profiles import ProfileStore

STATUS_SCHEMA = "desk_buddy.vision.status.v1"
REQUEST_SCHEMA = "desk_buddy.vision.detect-request.v1"
STATUS_TOPIC = "vision/detector/status"
REQUEST_TOPIC = "vision/detector/request"
RESULT_TOPIC = "vision/detector/result"
PREVIEW_TIMEOUT_SECONDS = 120
WORKFLOW_TIMEOUT_SECONDS = 240
MAX_REQUESTS = 8
TERMINAL_CACHE_SIZE = 256
TERMINAL_TTL_SECONDS = 120


@dataclass
class RequestRecord:
    key: tuple[str, str]
    request: dict
    received: float
    stage: str = "waiting_photo"


@dataclass(frozen=True)
class WorkItem:
    action_id: str
    prompt: str
    jpeg: bytes
    result_topic: str
    request: dict
    record: RequestRecord | None = None


class DetectorService:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.launch_id = str(config["launch_id"])
        self.owner_pid = int(config.get("owner_pid") or 0)
        self.detector = HuggingFaceDetector(config["model"])
        self.client = None
        self.stop_event = threading.Event()
        self.connected_event = threading.Event()
        self.connect_error = ""
        self.jobs: queue.Queue[WorkItem | None] = queue.Queue(maxsize=1)
        self.requests: dict[tuple[str, str], RequestRecord] = {}
        self._lock = threading.RLock()
        self._terminal: OrderedDict[tuple[str, str], tuple[float, dict]] = OrderedDict()
        self._outbox: OrderedDict[tuple[str, str], dict] = OrderedDict()
        default_profiles = Path(str(config["model"].get("cache_dir") or ".")).parent / "calibrations"
        self.profiles = ProfileStore(config.get("calibration_dir") or default_profiles)
        self.motion = CommandSequencer(self._publish_command, timeout_seconds=30.0)
        self._perch_mutations: dict[tuple[str, str], str] = {}
        self._subscription_mids: set[int] = set()
        self._subscriptions_ready = False
        routes = tuple(config.get("robot_routes", ()))
        self.command_routes = {
            str(route["command_topic"]): dict(route)
            for route in routes
            if isinstance(route, dict) and route.get("command_topic")
        }
        self.photo_routes = {
            str(route["photo_topic"]): dict(route)
            for route in routes
            if isinstance(route, dict) and route.get("photo_topic")
        }
        self.event_routes = {
            str(route["event_topic"]): dict(route)
            for route in routes
            if isinstance(route, dict) and route.get("event_topic")
        }
        self.last_error = ""
        self.loaded = False
        self.processing = False

    def run(self) -> int:
        import paho.mqtt.client as mqtt

        broker = self.config["mqtt"]
        username = os.environ.get(str(broker["username_env"]), "")
        password = os.environ.get(str(broker["password_env"]), "")
        if not username or not password:
            raise RuntimeError("vision MQTT credentials are missing")
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="desk-buddy-vision-detector",
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(username, password)
        client.will_set(STATUS_TOPIC, json.dumps(self._status("offline")), qos=1, retain=True)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_subscribe = self._on_subscribe
        client.on_disconnect = self._on_disconnect
        self.client = client
        inference_thread = None
        loop_started = False
        clean_exit = False
        try:
            client.connect(str(broker["host"]), int(broker["port"]), keepalive=30)
            client.loop_start()
            loop_started = True
            if not self.connected_event.wait(10):
                raise RuntimeError("MQTT broker did not acknowledge the detector within 10 seconds")
            if self.connect_error:
                raise RuntimeError(self.connect_error)
            self._publish_status("loading")
            self.detector.load()
            with self._lock:
                self.loaded = True
            inference_thread = threading.Thread(
                target=self._work_loop, name="detector-inference", daemon=True,
            )
            inference_thread.start()
            self._publish_status("ready")

            def stop(*_args) -> None:
                self.stop_event.set()

            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGTERM, stop)
                signal.signal(signal.SIGINT, stop)
            while not self.stop_event.wait(0.25):
                if not self._owner_alive():
                    break
                self._expire_requests()
            clean_exit = True
            return 0
        except Exception as exc:
            self.last_error = _safe_error(exc)
            if loop_started and not self.connect_error:
                self._publish_status("error")
            raise
        finally:
            self.stop_event.set()
            if inference_thread is not None:
                try:
                    self.jobs.put_nowait(None)
                except queue.Full:
                    # Inference gets the bounded join below. The daemon thread
                    # must never make process shutdown wait indefinitely.
                    pass
                inference_thread.join(timeout=5)
            if clean_exit:
                self._publish_status("offline")
            if loop_started:
                client.disconnect()
                client.loop_stop()

    def _owner_alive(self) -> bool:
        """Keep a serving worker scoped to the Studio process that launched it."""
        if self.owner_pid <= 0:
            return True  # Backward-compatible configs used by worker tests/tools.
        if os.name == "posix":
            return os.getppid() == self.owner_pid
        try:
            os.kill(self.owner_pid, 0)
        except OSError:
            return False
        return True

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        code = getattr(reason_code, "value", reason_code)
        if code != 0:
            self.connect_error = f"MQTT connection refused: {reason_code}"
            self.last_error = self.connect_error
            self.connected_event.set()
            return
        with self._lock:
            self._subscriptions_ready = False
            self._subscription_mids.clear()
            self.connect_error = ""
            for topic in (REQUEST_TOPIC, *self.command_routes, *self.event_routes, *self.photo_routes):
                rc, mid = client.subscribe(topic, qos=1)
                if rc != 0:
                    self.connect_error = f"Could not subscribe to {topic}"
                else:
                    self._subscription_mids.add(mid)
            if self.connect_error:
                self.connected_event.set()

    def _on_subscribe(self, _client, _userdata, mid, reason_codes, _properties) -> None:
        with self._lock:
            if mid not in self._subscription_mids:
                return
            self._subscription_mids.remove(mid)
            if not reason_codes or any(getattr(code, "value", code) >= 128 for code in reason_codes):
                self.connect_error = "The broker rejected a detector subscription"
                self.last_error = self.connect_error
            if self.connect_error:
                self.connected_event.set()
                self._publish_status("error")
            elif not self._subscription_mids:
                self._subscriptions_ready = True
                self.connected_event.set()
                self._flush_outbox()
                if self.loaded:
                    self._publish_status("busy" if self.processing else "ready")

    def _on_disconnect(self, _client, _userdata, _flags, _reason, _properties) -> None:
        self.motion.disconnected()
        with self._lock:
            self._subscriptions_ready = False
            self._subscription_mids.clear()
            records = tuple(
                record for record in self.requests.values()
                if record.stage == "waiting_photo"
            )
        for record in records:
            self._publish_failure(record.key[0], record.key[1], "connection_lost",
                                  "The detector lost its MQTT connection.", record=record)

    def _on_message(self, _client, _userdata, message) -> None:
        payload = bytes(message.payload)
        if message.topic == REQUEST_TOPIC:
            self._receive_local(payload)
            return
        if message.topic in self.command_routes:
            self._receive_robot_request(message.topic, payload)
            return
        if message.topic in self.event_routes:
            self._receive_robot_event(message.topic, payload)
            return
        if message.topic in self.photo_routes:
            self._receive_robot_frame(message.topic, payload)

    def _receive_robot_request(self, topic: str, payload: bytes) -> None:
        route = self.command_routes[topic]
        result_topic = str(route.get("result_topic") or "")
        if not result_topic:
            return
        try:
            body = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(body, dict):
            return
        action = str(body.get("action") or "")
        action_id = _action_id(body)
        event_topic = str(route.get("event_topic") or "")
        robot = str(route.get("robot") or "")
        if (
            action == "calibrate"
            and body.get("calibration_type") in {
                "perch_elbow_angle", "perch_wrist_angle", "perch_twist_angle",
            }
            and action_id and event_topic
        ):
            with self._lock:
                self._perch_mutations[(event_topic, action_id)] = robot
            return
        if action not in {"detect_object", "calibrate_depth"}:
            return
        if body.get("sender") in {"firmware", "visual_ai"}:
            return
        prompt = body.get("phrase", "")
        if not action_id or (action == "detect_object" and (not isinstance(prompt, str) or not prompt.strip())):
            if action_id:
                self._publish_failure(
                    result_topic, action_id, "invalid_request",
                    "A scalar detection phrase is required." if action == "detect_object"
                    else "A calibration action ID is required.",
                    robot=robot, result_type=action,
                )
            return
        if "use_model" in body and not isinstance(body.get("use_model"), bool):
            self._publish_failure(
                result_topic, action_id, "invalid_request",
                "use_model must be a JSON boolean.", robot=robot, result_type=action,
            )
            return
        if self._known((result_topic, action_id)):
            return
        requested_model = body.get("model_name")
        if requested_model and requested_model not in {
            self.detector.model_id, self.detector.catalog_id,
        }:
            self._publish_failure(
                result_topic, action_id, "model_mismatch",
                "The requested model is not active.",
                robot=str(route.get("robot") or ""),
            )
            return
        request = dict(body)
        request["_robot"] = robot
        request["_result_topic"] = result_topic
        request["_command_topic"] = topic
        request["_event_topic"] = event_topic
        self._admit(result_topic, action_id, request)

    def _receive_robot_event(self, topic: str, payload: bytes) -> None:
        route = self.event_routes[topic]
        command_topic = str(route.get("command_topic") or "")
        result_topic = str(route.get("result_topic") or "")
        robot = str(route.get("robot") or "")
        if not command_topic or not result_topic:
            return
        try:
            body = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(body, dict) or body.get("sender") != "firmware":
            return
        action_id = _action_id(body)
        self.motion.handle_event(topic, body)
        status = str(body.get("status") or "")
        with self._lock:
            changed_robot = self._perch_mutations.pop((topic, action_id), None) \
                if status in {"completed", "failed"} else None
        if changed_robot and status == "completed":
            if self.profiles.invalidate(changed_robot):
                self._publish_status("busy" if self.processing else "ready")
        if status != "failed":
            return
        with self._lock:
            pending = self.requests.get((result_topic, action_id))
        if pending is None:
            return
        error = body.get("error")
        code = "robot_capture_failed"
        message = "The robot failed to capture or publish the requested photo."
        if isinstance(error, dict):
            code = str(error.get("code") or code)
            message = str(error.get("message") or message)
        self._publish_failure(
            result_topic, action_id, code, message, robot=robot, record=pending,
        )

    def _receive_local(self, payload: bytes) -> None:
        try:
            frame = decode_frame(payload)
            metadata = frame.metadata
            if metadata.get("schema") != REQUEST_SCHEMA:
                raise ValueError("unsupported local detection request schema")
            action_id = _action_id(metadata)
            prompt = metadata.get("prompt")
            if not action_id or not isinstance(prompt, str) or not prompt.strip():
                raise ValueError("local request needs an action ID and scalar prompt")
            if metadata.get("use_model") is True:
                self._publish_failure(
                    RESULT_TOPIC, action_id, "automatic_execution_not_available",
                    "Automatic movement is only available from a correlated robot-camera request.",
                )
                return
            requested_model = metadata.get("model_name")
            if requested_model and requested_model not in {
                self.detector.model_id, self.detector.catalog_id,
            }:
                self._publish_failure(
                    RESULT_TOPIC, action_id, "model_mismatch",
                    "The requested model is not active.",
                )
                return
            self._enqueue(WorkItem(action_id, prompt.strip(), frame.jpeg, RESULT_TOPIC, metadata))
        except Exception as exc:
            action_id = ""
            try:
                metadata, _jpeg = decode_metadata(payload)
                action_id = _action_id(metadata)
            except Exception:
                pass
            if action_id:
                self._publish_failure(RESULT_TOPIC, action_id, "invalid_image", _safe_error(exc))

    def _receive_robot_frame(self, topic: str, payload: bytes) -> None:
        route = self.photo_routes[topic]
        command_topic = str(route.get("command_topic") or "")
        result_topic = str(route.get("result_topic") or "")
        robot = str(route.get("robot") or "")
        if not command_topic or not result_topic:
            return
        try:
            metadata, jpeg = decode_metadata(payload)
        except ValueError:
            return  # Never attribute uncorrelatable traffic to another request.
        action_id = _action_id(metadata)
        with self._lock:
            record = self.requests.get((result_topic, action_id))
            if record is None or record.stage != "waiting_photo":
                return
        try:
            # Reuse the extracted JPEG instead of copying the full frame twice.
            frame = BinaryFrame(metadata, jpeg)
            validate_photo_frame(frame)
            expected_type = str(record.request.get("action") or "")
            if expected_type and metadata.get("type") not in {None, expected_type}:
                raise ValueError("photo type does not match the admitted request")
        except ValueError as exc:
            self._publish_failure(result_topic, action_id, "invalid_image",
                                  f"The robot published an invalid photo: {_safe_error(exc)}",
                                  robot=robot, record=record)
            return
        self._enqueue(WorkItem(action_id, str(record.request.get("phrase") or "").strip(),
                               frame.jpeg, result_topic, record.request, record))

    def _prune_terminal(self) -> None:
        cutoff = time.monotonic() - TERMINAL_TTL_SECONDS
        while self._terminal and next(iter(self._terminal.values()))[0] <= cutoff:
            self._terminal.popitem(last=False)

    def _known(self, key: tuple[str, str]) -> bool:
        with self._lock:
            self._prune_terminal()
            return key in self.requests or key in self._terminal

    def _admit(self, topic: str, action_id: str, request: dict) -> RequestRecord | None:
        key = (topic, action_id)
        with self._lock:
            if self._known(key):
                return None
            robot = str(request.get("_robot") or "")
            if len(self.requests) >= MAX_REQUESTS or any(
                str(record.request.get("_robot") or "") == robot
                for record in self.requests.values()
            ):
                self._publish_failure(topic, action_id, "detector_busy",
                                      "The detector already has an outstanding request for this source or is full.", robot=robot)
                return None
            record = RequestRecord(key, request, time.monotonic())
            self.requests[key] = record
            return record

    def _enqueue(self, item: WorkItem) -> None:
        record = item.record or self._admit(item.result_topic, item.action_id, item.request)
        if record is None:
            return
        with self._lock:
            if self.requests.get(record.key) is not record or record.stage != "waiting_photo":
                return
            item = WorkItem(item.action_id, item.prompt, item.jpeg, item.result_topic, item.request, record)
            try:
                self.jobs.put_nowait(item)
                record.stage = "queued"
            except queue.Full:
                self._publish_failure(item.result_topic, item.action_id, "detector_busy",
                                      "The detector image queue is full.", record=record)

    def _work_loop(self) -> None:
        while True:
            item = self.jobs.get()
            if item is None:
                return
            with self._lock:
                if self.requests.get(item.record.key) is not item.record:
                    continue
                if time.monotonic() - item.record.received >= self._timeout_for(item.record):
                    self._publish_failure(item.result_topic, item.action_id, "detection_timeout",
                                          "The queued detection expired.", record=item.record)
                    continue
                item.record.stage = "running"
                self.processing = True
            self._publish_status("busy")
            try:
                if item.request.get("action") == "calibrate_depth":
                    self._calibrate_item(item)
                else:
                    self._detect_item(item)
                self.last_error = ""
            except CalibrationError as exc:
                is_calibration = item.request.get("action") == "calibrate_depth"
                self._publish_failure(
                    item.result_topic, item.action_id, exc.code, str(exc),
                    robot=str(item.request.get("_robot") or ""), record=item.record,
                    result_type="calibrate_depth" if is_calibration else "detect_object",
                    stage="calibration_failed" if is_calibration else "reach_and_grab_failed",
                    details={"calibration_candidates": list(exc.candidates)},
                )
            except Exception as exc:
                self.last_error = _safe_error(exc)
                self._publish_failure(
                    item.result_topic, item.action_id, "inference_failed",
                    self.last_error, robot=str(item.request.get("_robot") or ""), record=item.record,
                    result_type=str(item.request.get("action") or "detect_object"),
                )
            finally:
                with self._lock:
                    self.processing = False
                self._publish_status("ready")

    def _calibrate_item(self, item: WorkItem) -> None:
        robot = str(item.request.get("_robot") or "")
        result = calibrate(item.jpeg)
        try:
            _child_id, response = self.motion.execute(
                command_topic=str(item.request["_command_topic"]),
                event_topic=str(item.request["_event_topic"]),
                operation_id=item.action_id, index=0, action="calibrationvalues",
            )
        except MotionError as exc:
            raise CalibrationError(exc.code, str(exc), candidates=result.candidates) from exc
        values = response.get("calibrationvalues")
        if not isinstance(values, dict) or values.get("perch_configured") is not True:
            raise CalibrationError(
                "perch_not_configured",
                "Save elbow, wrist, and twist as the camera perch before calibrating the grid.",
                candidates=result.candidates,
            )
        perch = self._perch(values)
        profile = self.profiles.create(
            robot=robot, width=result.width, height=result.height,
            points=result.points, grid=result.grid, perch=perch, algorithm=ALGORITHM,
        )
        self.profiles.save(profile)
        self._publish_status("busy")
        self._publish_result(item.result_topic, {
            "sender": "visual_ai", "action_id": item.action_id,
            "status": "completed", "type": "calibrate_depth",
            "stage": "calibration_completed", "profile_id": profile["profile_id"],
            "calibration_schema": profile["schema"],
            "calibration_points": result.points, "calibration_grid": result.grid,
            "calibration_candidates": list(result.candidates),
            "calibration_profile": {
                "profile_id": profile["profile_id"], "created_at": profile["created_at"],
                "image_width": result.width, "image_height": result.height,
                "capture_pose": profile["capture_pose"], "algorithm": ALGORITHM,
            },
        }, record=item.record)

    def _detect_item(self, item: WorkItem) -> None:
        robot = str(item.request.get("_robot") or "")
        batch = self.detector.detect(item.jpeg, item.prompt)
        detections = batch["detections"]
        if not detections:
            self._publish_failure(
                item.result_topic, item.action_id, "no_detection",
                f"No {item.prompt!r} detection met the confidence threshold.",
                batch=batch, robot=robot, record=item.record,
            )
            return
        selected = detections[0]
        common = {
            "detection_batch": batch, "selected_detection": selected,
            "model_id": self.detector.model_id, "catalog_id": self.detector.catalog_id,
            "revision": self.detector.revision,
        }
        if item.request.get("use_model") is not True:
            self._publish_result(item.result_topic, {
                "sender": "visual_ai", "action_id": item.action_id,
                "status": "completed", "type": "detect_object",
                "stage": "detection_only", **common,
            }, record=item.record)
            return

        profile = self.profiles.active(robot)
        if profile is None:
            self._workflow_failure(item, "missing_calibration_profile",
                                   "Run the nine-point visual calibration for this robot first.",
                                   common=common)
            return
        if (
            int(profile.get("image_width") or 0) != int(batch.get("image_width") or 0)
            or int(profile.get("image_height") or 0) != int(batch.get("image_height") or 0)
        ):
            self._workflow_failure(item, "calibration_resolution_mismatch",
                                   "The camera resolution changed; recalibrate the nine-point grid.",
                                   common=common)
            return
        target = project(selected.get("box_px", ()), profile)
        if not self._valid_target(target):
            reason = (target.get("rejection_reasons") or ["invalid_target"])[0]
            self._workflow_failure(item, reason,
                                   "The detected object's table-contact point is outside the calibrated workspace.",
                                   common=common, target=target)
            return

        self._publish_progress(item, "target_planned", "Calibrated target ready.", target=target)
        attempted_motion = False
        failed_step = "calibrationvalues"
        try:
            _child, response = self.motion.execute(
                command_topic=str(item.request["_command_topic"]),
                event_topic=str(item.request["_event_topic"]),
                operation_id=item.action_id, index=1, action="calibrationvalues",
            )
            values = response.get("calibrationvalues")
            if not isinstance(values, dict) or values.get("motion_calibration_ready") is not True:
                raise MotionError("motion_calibration_not_ready", "Base rotation and table-height IK must be calibrated.")
            try:
                current_perch = self._perch(values)
            except CalibrationError as exc:
                raise MotionError(exc.code, str(exc)) from exc
            if values.get("perch_configured") is not True or current_perch != profile["capture_pose"]["perch"]:
                raise MotionError("calibration_pose_mismatch", "The saved camera perch changed; recalibrate the grid.")

            attempted_motion = True
            failed_step = "baseRotate"
            self._publish_progress(item, "executing_base", "Rotating the base.", target=target)
            self.motion.execute(
                command_topic=str(item.request["_command_topic"]),
                event_topic=str(item.request["_event_topic"]),
                operation_id=item.action_id, index=2, action="baseRotate",
                fields={"controlType": "ANGLE", "value": target["angle_deg"] % 360.0, "speed": "veryslow"},
            )
            failed_step = "controlik"
            self._publish_progress(item, "executing_ik", "Moving to the calibrated table depth.", target=target)
            self.motion.execute(
                command_topic=str(item.request["_command_topic"]),
                event_topic=str(item.request["_event_topic"]),
                operation_id=item.action_id, index=3, action="controlik",
                fields={"distance": target["distance_mm"], "z_height": 0.0},
            )
            failed_step = "gripper"
            self._publish_progress(item, "executing_grab", "Closing the gripper.", target=target)
            self.motion.execute(
                command_topic=str(item.request["_command_topic"]),
                event_topic=str(item.request["_event_topic"]),
                operation_id=item.action_id, index=4, action="gripper",
                fields={"command": "GRAB"},
            )
        except MotionError as exc:
            self._workflow_failure(
                item, exc.code, str(exc), common=common, target=target,
                failed_step=failed_step,
                physical_state="possibly_moved" if attempted_motion or exc.uncertain else "unchanged",
            )
            return

        self._publish_result(item.result_topic, {
            "sender": "visual_ai", "action_id": item.action_id,
            "status": "completed", "type": "detect_object",
            "stage": "reach_and_grab_completed", "grab_status": "completed",
            "physical_state": "moved", "ik_target": target, **common,
        }, record=item.record)

    @staticmethod
    def _perch(values: Mapping[str, object]) -> dict[str, int]:
        effective = values.get("perch_effective")
        if not isinstance(effective, Mapping):
            raise CalibrationError("missing_perch_values", "Firmware did not return the effective perch pose.")
        try:
            # ActionPerch uses C++ static_cast<int>, so truncate here too.  The
            # profile fingerprint then describes the angles actually moved.
            return {name: int(float(effective[name])) for name in ("ELBOW", "WRIST", "TWIST")}
        except (KeyError, TypeError, ValueError) as exc:
            raise CalibrationError("invalid_perch_values", "Firmware returned an invalid perch pose.") from exc

    @staticmethod
    def _valid_target(target: Mapping[str, Any]) -> bool:
        values = (target.get("angle_deg"), target.get("distance_mm"), target.get("z_height_mm"))
        try:
            angle, distance, z_height = (float(value) for value in values)
        except (TypeError, ValueError):
            return False
        return (
            target.get("accepted") is True and all(math.isfinite(value) for value in (angle, distance, z_height))
            and -30.0 <= angle <= 30.0 and 0.0 <= distance <= 120.0 and z_height == 0.0
        )

    def _workflow_failure(
        self, item: WorkItem, code: str, message: str, *, common: Mapping[str, Any],
        target: Mapping[str, Any] | None = None, failed_step: str = "planning",
        physical_state: str = "unchanged",
    ) -> None:
        result = {
            "sender": "visual_ai", "action_id": item.action_id,
            "status": "failed", "type": "detect_object",
            "stage": "reach_and_grab_failed", "error": {"code": code, "message": message},
            "failed_step": failed_step, "physical_state": physical_state, **dict(common),
        }
        if target is not None:
            result["ik_target"] = dict(target)
        self._publish_result(item.result_topic, result, record=item.record)

    def _publish_progress(self, item: WorkItem, stage: str, message: str, *, target=None) -> None:
        result = {
            "sender": "visual_ai", "action_id": item.action_id,
            "status": "in_progress", "type": "detect_object",
            "stage": stage, "log": message,
        }
        if target is not None:
            result["ik_target"] = dict(target)
        self._publish_json(item.result_topic, result)

    def _publish_failure(
        self, topic: str, action_id: str, code: str, message: str, *,
        batch: dict | None = None, robot: str = "", record: RequestRecord | None = None,
        result_type: str = "detect_object", stage: str = "detection_only",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        result = {
            "sender": "visual_ai",
            "action_id": action_id,
            "status": "failed",
            "type": result_type,
            "stage": stage,
            "error": {"code": code, "message": message},
            "model_id": self.detector.model_id,
            "catalog_id": self.detector.catalog_id,
            "revision": self.detector.revision,
        }
        if batch is not None:
            result["detection_batch"] = batch
            result["selected_detection"] = None
        if robot:
            result["robot"] = robot
        if details:
            result.update(dict(details))
        self._publish_result(topic, result, record=record)

    def _publish_result(self, topic: str, result: dict, *, record: RequestRecord | None = None) -> None:
        key = (topic, str(result["action_id"]))
        with self._lock:
            self._prune_terminal()
            if record is not None:
                if self.requests.get(key) is not record:
                    return
                timeout = self._timeout_for(record)
                if time.monotonic() - record.received >= timeout:
                    result = {
                        "sender": "visual_ai", "action_id": key[1], "status": "failed",
                        "type": "detect_object", "stage": "detection_only",
                        "model_id": self.detector.model_id, "catalog_id": self.detector.catalog_id,
                        "revision": self.detector.revision,
                        "error": {
                            "code": "photo_timeout" if record.stage == "waiting_photo" else "detection_timeout",
                            "message": f"The vision request exceeded its {int(timeout)}-second deadline.",
                        },
                    }
                robot = str(record.request.get("_robot") or "")
                if robot:
                    result["robot"] = robot
                self.requests.pop(key)
                record.stage = "terminal"
            elif key in self.requests or key in self._terminal:
                return
            self._terminal[key] = (time.monotonic(), dict(result))
            while len(self._terminal) > TERMINAL_CACHE_SIZE:
                self._terminal.popitem(last=False)
        self._publish_json(topic, result, terminal_key=key)

    def _publish_json(self, topic: str, result: Mapping[str, Any], *, terminal_key=None) -> None:
        if self.client is None:
            if terminal_key:
                self._outbox[terminal_key] = {"topic": topic, "result": dict(result)}
            return
        try:
            info = self.client.publish(topic, json.dumps(result, separators=(",", ":")), qos=1)
            if getattr(info, "rc", 0) not in (None, 0) and terminal_key:
                self._outbox[terminal_key] = {"topic": topic, "result": dict(result)}
        except Exception:
            if terminal_key:
                self._outbox[terminal_key] = {"topic": topic, "result": dict(result)}

    def _flush_outbox(self) -> None:
        with self._lock:
            pending = tuple(self._outbox.items())
        for key, item in pending:
            try:
                info = self.client.publish(
                    item["topic"], json.dumps(item["result"], separators=(",", ":")), qos=1,
                )
                if getattr(info, "rc", 0) in (None, 0):
                    with self._lock:
                        self._outbox.pop(key, None)
            except Exception:
                return

    def _publish_command(self, topic: str, payload: str, **options):
        if self.client is None:
            raise RuntimeError("MQTT client is unavailable")
        return self.client.publish(topic, payload, **options)

    def _status(self, state: str) -> dict:
        return {
            "schema": STATUS_SCHEMA,
            "service_id": "detector",
            "launch_id": self.launch_id,
            "process_state": "running" if state != "offline" else "stopped",
            "state": state,
            "ready": state in {"ready", "busy"},
            "busy": state == "busy",
            "device": self.detector.device,
            "model_id": self.detector.model_id,
            "catalog_id": self.detector.catalog_id,
            "revision": self.detector.revision,
            "last_error": self.last_error,
            "calibration_profiles": self.profiles.active_ids(),
        }

    def _publish_status(self, state: str) -> None:
        with self._lock:
            if state in {"ready", "busy"} and not (self.loaded and self._subscriptions_ready):
                return
            if self.client is not None:
                self.client.publish(STATUS_TOPIC, json.dumps(self._status(state), separators=(",", ":")), qos=1, retain=True)

    def _expire_requests(self) -> None:
        with self._lock:
            records = tuple(self.requests.values())
            self._prune_terminal()
        for record in records:
            timeout = self._timeout_for(record)
            if time.monotonic() - record.received >= timeout:
                waiting = record.stage == "waiting_photo"
                self._publish_failure(
                    record.key[0], record.key[1],
                    "photo_timeout" if waiting else "detection_timeout",
                    f"The robot photo did not arrive within {int(timeout)} seconds." if waiting
                    else f"Vision did not finish within {int(timeout)} seconds.",
                    robot=str(record.request.get("_robot") or ""), record=record,
                    result_type=str(record.request.get("action") or "detect_object"),
                )

    @staticmethod
    def _timeout_for(record: RequestRecord) -> float:
        return WORKFLOW_TIMEOUT_SECONDS if (
            record.request.get("action") == "calibrate_depth"
            or record.request.get("use_model") is True
        ) else PREVIEW_TIMEOUT_SECONDS


def _action_id(value: dict) -> str:
    raw = value.get("action_id")
    return str(raw) if isinstance(raw, (str, int)) and str(raw) else ""


def _safe_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text[:500] or type(exc).__name__
