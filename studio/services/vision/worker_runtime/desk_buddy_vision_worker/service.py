"""Standalone MQTT service for one loaded zero-shot detector."""

from __future__ import annotations

import json
import os
import queue
import signal
import threading
import time
from dataclasses import dataclass

from .detector import HuggingFaceDetector
from .frames import BinaryFrame, decode_frame, decode_metadata, validate_photo_frame

STATUS_SCHEMA = "desk_buddy.vision.status.v1"
REQUEST_SCHEMA = "desk_buddy.vision.detect-request.v1"
STATUS_TOPIC = "vision/detector/status"
REQUEST_TOPIC = "vision/detector/request"
RESULT_TOPIC = "vision/detector/result"


@dataclass(frozen=True)
class WorkItem:
    action_id: str
    prompt: str
    jpeg: bytes
    result_topic: str
    request: dict


class DetectorService:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.launch_id = str(config["launch_id"])
        self.detector = HuggingFaceDetector(config["model"])
        self.client = None
        self.stop_event = threading.Event()
        self.connected_event = threading.Event()
        self.connect_error = ""
        self.jobs: queue.Queue[WorkItem | None] = queue.Queue(maxsize=1)
        self.requests: dict[tuple[str, str], tuple[dict, float]] = {}
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

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        code = getattr(reason_code, "value", reason_code)
        if code != 0:
            self.connect_error = f"MQTT connection refused: {reason_code}"
            self.last_error = self.connect_error
            self.connected_event.set()
            return
        client.subscribe(REQUEST_TOPIC, qos=1)
        for topic in (*self.command_routes, *self.event_routes, *self.photo_routes):
            client.subscribe(topic, qos=1)
        if self.loaded:
            # paho re-enters this callback after reconnecting. Republish the
            # live state so the retained Last Will cannot strand Studio in an
            # offline state after the broker comes back.
            self._publish_status("busy" if self.processing else "ready")
        self.connected_event.set()

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
        if body.get("action") != "detect_object":
            return
        if body.get("sender") in {"firmware", "visual_ai"}:
            return
        action_id = _action_id(body)
        prompt = body.get("phrase")
        if not action_id or not isinstance(prompt, str) or not prompt.strip():
            if action_id:
                self._publish_failure(
                    result_topic, action_id, "invalid_request",
                    "A scalar detection phrase is required.",
                    robot=str(route.get("robot") or ""),
                )
            return
        if body.get("use_model") is True:
            self._publish_failure(
                result_topic, action_id, "automatic_execution_not_available",
                "Automatic reach and grab is not available in basic vision.",
                robot=str(route.get("robot") or ""),
            )
            return
        key = (topic, action_id)
        if key in self.requests:
            self.requests.pop(key, None)
            self._publish_failure(
                result_topic, action_id, "duplicate_action_id",
                "A pending detection already uses that action ID.",
                robot=str(route.get("robot") or ""),
            )
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
        request["_robot"] = str(route.get("robot") or "")
        request["_result_topic"] = result_topic
        self.requests[key] = (request, time.monotonic())

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
        if (
            not isinstance(body, dict)
            or body.get("sender") != "firmware"
            or body.get("status") != "failed"
        ):
            return
        action_id = _action_id(body)
        pending = self.requests.pop((command_topic, action_id), None)
        if pending is None:
            return
        error = body.get("error")
        code = "robot_capture_failed"
        message = "The robot failed to capture or publish the requested photo."
        if isinstance(error, dict):
            code = str(error.get("code") or code)
            message = str(error.get("message") or message)
        self._publish_failure(
            result_topic, action_id, code, message, robot=robot,
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
                    "Automatic reach and grab is not available in basic vision.",
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
            frame: BinaryFrame = decode_frame(payload)
            validate_photo_frame(frame)
        except ValueError as exc:
            candidates = [key for key in self.requests if key[0] == command_topic]
            if len(candidates) == 1:
                expected = candidates[0]
                self.requests.pop(expected, None)
                self._publish_failure(
                    result_topic, expected[1], "invalid_image",
                    f"The robot published an invalid photo: {_safe_error(exc)}",
                    robot=robot,
                )
            return
        action_id = _action_id(frame.metadata)
        pending = self.requests.pop((command_topic, action_id), None)
        if not action_id:
            return
        if pending is None:
            candidates = [key for key in self.requests if key[0] == command_topic]
            if len(candidates) == 1:
                expected = candidates[0]
                self.requests.pop(expected, None)
                self._publish_failure(
                    result_topic, expected[1], "action_id_mismatch",
                    "The robot photo did not match the pending detection request.",
                    robot=robot,
                )
            return
        request, _received = pending
        self._enqueue(WorkItem(
            action_id, str(request["phrase"]).strip(), frame.jpeg, result_topic, request,
        ))

    def _enqueue(self, item: WorkItem) -> None:
        try:
            self.jobs.put_nowait(item)
        except queue.Full:
            self._publish_failure(
                item.result_topic, item.action_id, "detector_busy",
                "The detector is already processing an image.",
                robot=str(item.request.get("_robot") or ""),
            )

    def _work_loop(self) -> None:
        while True:
            item = self.jobs.get()
            if item is None:
                return
            self.processing = True
            self._publish_status("busy")
            try:
                batch = self.detector.detect(item.jpeg, item.prompt)
                detections = batch["detections"]
                robot = str(item.request.get("_robot") or "")
                if not detections:
                    self._publish_failure(
                        item.result_topic, item.action_id, "no_detection",
                        f"No {item.prompt!r} detection met the confidence threshold.",
                        batch=batch, robot=robot,
                    )
                else:
                    result = {
                        "sender": "visual_ai",
                        "action_id": item.action_id,
                        "status": "completed",
                        "type": "detect_object",
                        "stage": "detection_only",
                        "detection_batch": batch,
                        "selected_detection": detections[0],
                        "model_id": self.detector.model_id,
                        "catalog_id": self.detector.catalog_id,
                        "revision": self.detector.revision,
                    }
                    if robot:
                        result["robot"] = robot
                    self._publish_result(item.result_topic, result)
                self.last_error = ""
            except Exception as exc:
                self.last_error = _safe_error(exc)
                self._publish_failure(
                    item.result_topic, item.action_id, "inference_failed",
                    self.last_error, robot=str(item.request.get("_robot") or ""),
                )
            finally:
                self.processing = False
                self._publish_status("ready")

    def _publish_failure(
        self, topic: str, action_id: str, code: str, message: str, *,
        batch: dict | None = None, robot: str = "",
    ) -> None:
        result = {
            "sender": "visual_ai",
            "action_id": action_id,
            "status": "failed",
            "type": "detect_object",
            "stage": "detection_only",
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
        self._publish_result(topic, result)

    def _publish_result(self, topic: str, result: dict) -> None:
        if self.client is not None:
            self.client.publish(topic, json.dumps(result, separators=(",", ":")), qos=1)

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
        }

    def _publish_status(self, state: str) -> None:
        if self.client is not None:
            self.client.publish(STATUS_TOPIC, json.dumps(self._status(state), separators=(",", ":")), qos=1, retain=True)

    def _expire_requests(self) -> None:
        cutoff = time.monotonic() - 120
        for key, (request, received) in tuple(self.requests.items()):
            if received < cutoff:
                self.requests.pop(key, None)
                self._publish_failure(
                    str(request.get("_result_topic") or ""), key[1],
                    "photo_timeout",
                    "The robot did not publish a correlated photo within 120 seconds.",
                    robot=str(request.get("_robot") or ""),
                )


def _action_id(value: dict) -> str:
    raw = value.get("action_id")
    return str(raw) if isinstance(raw, (str, int)) and str(raw) else ""


def _safe_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text[:500] or type(exc).__name__
