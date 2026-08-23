from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from typing import Mapping

import numpy as np

from tests.support import FakeBroker, wait_until
from tests.test_calibration import synthetic_calibration_jpeg

from desk_buddy_vision_protocol import (
    ArtifactInput,
    Detection,
    DetectionBatch,
    GeneratedArtifact,
    JobEnvelope,
    MQTTWorker,
    TopicLayout,
    VisionRequest,
    WorkerOutput,
)
from desk_buddy_vision.config import (
    AppConfig,
    CoordinatorConfig,
    ModelRoute,
    MQTTConfig,
    RobotConfig,
)
from desk_buddy_vision.coordinator import VisionCoordinator


class FakeDetector:
    model_id = "fake-detector"
    model_version = "1"
    job_kinds = ("detector.infer",)
    input_schemas = ("image.jpeg.v1",)
    output_schemas = ("detections.v1",)

    def handle(self, job: JobEnvelope, artifacts: Mapping[str, ArtifactInput]) -> WorkerOutput:
        self.assert_no_paths(job)
        detection = Detection.create(
            label=str(job.payload["phrase"]),
            score=0.95,
            bbox_px=(290, 220, 350, 300),
            image_width=640,
            image_height=480,
            source_model_id=self.model_id,
            source_model_version=self.model_version,
        )
        batch = DetectionBatch(
            image_width=640,
            image_height=480,
            phrase=str(job.payload["phrase"]),
            detections=(detection,),
            model_id=self.model_id,
            model_version=self.model_version,
        )
        return WorkerOutput(payload={"detection_batch": batch.as_dict()})

    @staticmethod
    def assert_no_paths(job: JobEnvelope) -> None:
        assert job.input_artifact_ids and all("/" not in artifact_id for artifact_id in job.input_artifact_ids)


class FakeDepth:
    model_id = "fake-depth"
    model_version = "1"
    job_kinds = ("depth.infer",)
    input_schemas = ("image.jpeg.v1",)
    output_schemas = ("depth.v1",)

    def handle(self, job: JobEnvelope, artifacts: Mapping[str, ArtifactInput]) -> WorkerOutput:
        values = np.linspace(0, 1, 120 * 160, dtype=np.float32).reshape(120, 160)
        buffer = BytesIO()
        np.save(buffer, values, allow_pickle=False)
        raw = buffer.getvalue()
        return WorkerOutput(
            payload={
                "depth": {
                    "output_schema": "depth.v1",
                    "width": 160,
                    "height": 120,
                    "dtype": "float32",
                    "min_value": 0.0,
                    "max_value": 1.0,
                    "units": "relative",
                    "near_is_high": True,
                    "source_model_id": self.model_id,
                    "source_model_version": self.model_version,
                }
            },
            artifacts=(
                GeneratedArtifact(
                    role="raw_depth",
                    kind="depth_raw",
                    mime_type="application/x-npy",
                    payload=raw,
                    width=160,
                    height=120,
                    dtype="float32",
                ),
                GeneratedArtifact(
                    role="normalized_depth",
                    kind="depth_normalized",
                    mime_type="application/x-npy",
                    payload=raw,
                    width=160,
                    height=120,
                    dtype="float32",
                ),
            ),
        )


class MQTTIntegrationTests(unittest.TestCase):
    def test_calibration_then_distributed_detection_workflow(self):
        with tempfile.TemporaryDirectory() as temporary:
            broker = FakeBroker()
            root = "desk_buddy_test"
            config = AppConfig(
                mqtt=MQTTConfig(host="fake", tls=False, topic_root=root),
                coordinator=CoordinatorConfig(
                    service_id="vision-coordinator",
                    data_dir=Path(temporary),
                    worker_timeout_seconds=5,
                ),
                robots=(RobotConfig(robot_id="robot-1", topic=f"{root}/robot-1"),),
                models=(
                    ModelRoute("fake-detector", "detector", "detector-service", True),
                    ModelRoute("fake-depth", "depth", "depth-service", True),
                ),
            )
            coordinator_transport = broker.client("coordinator")
            coordinator = VisionCoordinator(config=config, transport=coordinator_transport)
            coordinator_transport.set_message_handler(coordinator.handle_message)
            coordinator.start()

            detector_transport = broker.client("remote-detector-host")
            detector = MQTTWorker(
                service_id="detector-service",
                service_kind="detector",
                transport=detector_transport,
                handlers={"fake-detector": FakeDetector()},
                topics=TopicLayout(root),
            )
            detector_transport.set_message_handler(detector.handle_message)
            detector.start()

            depth_transport = broker.client("remote-depth-host")
            depth = MQTTWorker(
                service_id="depth-service",
                service_kind="depth",
                transport=depth_transport,
                handlers={"fake-depth": FakeDepth()},
                topics=TopicLayout(root),
            )
            depth_transport.set_message_handler(depth.handle_message)
            depth.start()

            gui = broker.client("gui")
            firmware = broker.client("firmware")
            try:
                wait_until(lambda: len(coordinator._capabilities) == 2)
                calibration_request = VisionRequest(
                    request_id="calibration-1",
                    robot_id="robot-1",
                    sender="gui",
                    kind="calibration",
                )
                gui.publish(TopicLayout(root).vision_request("robot-1"), calibration_request.as_dict(), qos=1)
                command = wait_until(
                    lambda: next(
                        (
                            message
                            for message in broker.published
                            if message.client_id == "coordinator"
                            and message.topic == f"{root}/robot-1/test"
                            and message.payload.startswith(b"{")
                            and message.json().get("action") == "calibrate_depth"
                        ),
                        None,
                    )
                ).json()
                jpeg = synthetic_calibration_jpeg()
                mixed = (
                    json.dumps(
                        {"sender": "firmware", "action_id": command["action_id"], "type": "photo"},
                        separators=(",", ":"),
                    )[:-1].encode("utf-8")
                    + b',"payload":'
                    + jpeg
                    + b"}"
                )
                firmware.publish(f"{root}/robot-1/test", mixed, qos=0)
                wait_until(lambda: self._terminal_event(broker, "calibration-1"))

                detect_request = VisionRequest(
                    request_id="detect-1",
                    robot_id="robot-1",
                    sender="gui",
                    kind="detect",
                    payload={"phrase": "blue block", "execute": False},
                )
                gui.publish(TopicLayout(root).vision_request("robot-1"), detect_request.as_dict(), qos=1)
                detect_command = wait_until(
                    lambda: next(
                        (
                            message
                            for message in broker.published
                            if message.client_id == "coordinator"
                            and message.topic == f"{root}/robot-1/test"
                            and message.payload.startswith(b"{")
                            and message.json().get("action") == "detect_object"
                        ),
                        None,
                    )
                ).json()
                detect_mixed = (
                    json.dumps(
                        {"sender": "firmware", "action_id": detect_command["action_id"], "type": "photo"},
                        separators=(",", ":"),
                    )[:-1].encode("utf-8")
                    + b',"payload":'
                    + jpeg
                    + b"}"
                )
                firmware.publish(f"{root}/robot-1/test", detect_mixed, qos=0)
                terminal = wait_until(lambda: self._terminal_event(broker, "detect-1"), timeout=10)
                self.assertEqual(terminal["status"], "completed")
                self.assertEqual(terminal["payload"]["motion_plan"]["safety"]["status"], "safe")
                self.assertIn("normalized_depth", terminal["payload"]["artifacts"])

                job_topics = {
                    message.topic
                    for message in broker.published
                    if message.client_id == "coordinator" and "/vision/service/" in message.topic
                }
                self.assertIn(f"{root}/vision/service/detector-service/request", job_topics)
                self.assertIn(f"{root}/vision/service/depth-service/request", job_topics)
                self.assertTrue(
                    any(
                        message.client_id == "remote-detector-host"
                        and message.topic == f"{root}/vision/artifact/request"
                        for message in broker.published
                    )
                )

                moving_request = VisionRequest(
                    request_id="detect-execute-1",
                    robot_id="robot-1",
                    sender="gui",
                    kind="detect",
                    payload={"phrase": "blue block", "execute": True},
                )
                motion_start = len(broker.published)
                gui.publish(TopicLayout(root).vision_request("robot-1"), moving_request.as_dict(), qos=1)
                moving_photo = wait_until(
                    lambda: next(
                        (
                            message
                            for message in broker.published[motion_start:]
                            if message.client_id == "coordinator"
                            and message.topic == f"{root}/robot-1/test"
                            and message.payload.startswith(b"{")
                            and message.json().get("action") == "detect_object"
                        ),
                        None,
                    )
                ).json()
                moving_mixed = (
                    json.dumps(
                        {"sender": "firmware", "action_id": moving_photo["action_id"], "type": "photo"},
                        separators=(",", ":"),
                    )[:-1].encode("utf-8")
                    + b',"payload":'
                    + jpeg
                    + b"}"
                )
                firmware.publish(f"{root}/robot-1/test", moving_mixed, qos=0)
                command_cursor = motion_start
                physical_actions = []
                while not physical_actions or physical_actions[-1] != "calibrationvalues":
                    published_command = wait_until(
                        lambda: next(
                            (
                                message
                                for message in broker.published[command_cursor:]
                                if message.client_id == "coordinator"
                                and message.topic == f"{root}/robot-1/test"
                                and message.payload.startswith(b"{")
                                and str(message.json().get("action_id", "")).startswith("rg-")
                            ),
                            None,
                        ),
                        timeout=10,
                    )
                    command_cursor = broker.published.index(published_command) + 1
                    command_body = published_command.json()
                    physical_actions.append(command_body["action"])
                    self.assertLess(len(published_command.payload), 512)
                    firmware.publish(
                        f"{root}/robot-1/test",
                        {
                            "sender": "firmware",
                            "action_id": command_body["action_id"],
                            "status": "completed",
                            **(
                                {"calibrationvalues": {"ELBOW_ANGLE": 120}}
                                if command_body["action"] == "calibrationvalues"
                                else {}
                            ),
                        },
                        qos=0,
                    )
                moving_terminal = wait_until(
                    lambda: self._terminal_event(broker, "detect-execute-1"), timeout=10
                )
                self.assertEqual(moving_terminal["status"], "completed")
                self.assertIn(
                    physical_actions,
                    (
                        ["controlik", "gripper", "calibrationvalues"],
                        ["baseRotate", "controlik", "gripper", "calibrationvalues"],
                    ),
                )

                before = len(
                    [
                        message
                        for message in broker.published
                        if message.client_id == "coordinator"
                        and message.topic == f"{root}/robot-1/test"
                        and message.payload.startswith(b"{")
                        and message.json().get("action") == "detect_object"
                    ]
                )
                gui.publish(TopicLayout(root).vision_request("robot-1"), detect_request.as_dict(), qos=1)
                duplicate = wait_until(
                    lambda: next(
                        (
                            message.json()
                            for message in reversed(broker.published)
                            if message.client_id == "coordinator"
                            and message.topic.endswith("/vision/event")
                            and message.payload.startswith(b"{")
                            and message.json().get("request_id") == "detect-1"
                            and message.json().get("payload", {}).get("duplicate")
                        ),
                        None,
                    )
                )
                self.assertEqual(duplicate["payload"]["artifacts"], terminal["payload"]["artifacts"])
                after = len(
                    [
                        message
                        for message in broker.published
                        if message.client_id == "coordinator"
                        and message.topic == f"{root}/robot-1/test"
                        and message.payload.startswith(b"{")
                        and message.json().get("action") == "detect_object"
                    ]
                )
                self.assertEqual(before, after)

                status_request = VisionRequest(
                    request_id="status-recover-1",
                    robot_id="robot-1",
                    sender="gui",
                    kind="status",
                    payload={"request_id": "detect-1"},
                )
                gui.publish(TopicLayout(root).vision_request("robot-1"), status_request.as_dict(), qos=1)
                recovered = wait_until(
                    lambda: self._terminal_event(broker, "status-recover-1"), timeout=5
                )
                self.assertEqual(recovered["payload"]["operation"]["status"], "completed")
                self.assertEqual(
                    recovered["payload"]["operation"]["result"]["artifacts"],
                    terminal["payload"]["artifacts"],
                )
            finally:
                detector.stop()
                depth.stop()
                coordinator.stop()

    @staticmethod
    def _terminal_event(broker: FakeBroker, request_id: str):
        for message in reversed(broker.published):
            if message.client_id != "coordinator" or not message.topic.endswith("/vision/event"):
                continue
            if not message.payload.startswith(b"{"):
                continue
            body = message.json()
            if body.get("request_id") == request_id and body.get("status") in {"completed", "failed"}:
                return body
        return None


if __name__ == "__main__":
    unittest.main()
