"""Post-implementation tests for the MQTT vision boundary and reusable IK."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from .contracts import (
    CONTRACT_SCHEMA, DEPTH_SCHEMA, ContractError, DepthMapV1, DetectionBatchV1,
    DetectionV1, IKTargetV1, JobEnvelope, JobEvent, VisionObservationV1, WorkerStatus,
)
from .depth.huggingface import compressed_depth, depth_array
from .frames import decode_artifact, encode_artifact
from .ik_control import (
    IKControl, LearnedModelCompatibility, build_grid, calibrate, target_from_prediction,
)
from .contracts import IKPredictionV1
from .model_builder import VisionStore, build_dataset_bundle, read_dataset_bundle
from .model_builder.inference import IKInferenceHandler
from .topics import ModelRoute, VisionTopics, WorkerRegistry
from .vision_controller import ControllerConfig, VisionController
from .worker import MQTTWorker, WorkerOutput


class FakeTransport:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[str, int]] = []
        self.published: list[tuple[str, object, int, bool]] = []

    def subscribe(self, topic: str, *, qos: int = 1) -> None:
        self.subscriptions.append((topic, qos))

    def publish(self, topic: str, payload, *, qos: int, retain: bool = False) -> bool:
        self.published.append((topic, payload, qos, retain))
        return True


class EchoHandler:
    model_id = "echo"
    model_version = "1"
    job_kinds = ("zero_shot.infer",)
    input_schemas = ()
    output_schemas = ("echo.v1",)

    def __init__(self) -> None:
        self.calls = 0

    def handle(self, job, artifacts):
        self.calls += 1
        return WorkerOutput({"ok": True})


def jpeg(width: int = 160, height: int = 120) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    output = BytesIO()
    image.save(output, "JPEG")
    return output.getvalue()


def observation(capture_id: str = "capture-1") -> VisionObservationV1:
    detection = DetectionV1.create(
        label="cup", score=0.9, bbox_px=(40, 30, 100, 90), image_width=160, image_height=120,
        model_id="detector", model_version="abc",
    )
    return VisionObservationV1(
        capture_id=capture_id, robot_id="robot", prompt="cup", detection=detection,
        depth_patch_64x64=(0.5,) * 4096, depth_statistics={"mean": 0.5},
        detector_model_id="detector", detector_model_version="abc",
        depth_model_id="depth", depth_model_version="def", calibration_profile_id="cal-1",
    )


def calibration_grid():
    points = {}
    for column, x in (("left", 20), ("center", 80), ("right", 140)):
        for depth, y in ((0, 110), (60, 60), (120, 10)):
            points[f"{column}_{depth}"] = {"x": x, "y": y}
    return {**build_grid(points), "profile_id": "cal-1"}


class ContractAndFrameTests(unittest.TestCase):
    def test_frame_integrity_and_packet_limit(self) -> None:
        frame = encode_artifact(job_id="job", artifact_id="a", role="image", mime_type="image/jpeg", payload=b"hello")
        artifact = decode_artifact(frame)
        self.assertEqual(artifact.payload, b"hello")
        damaged = frame[:-1] + bytes((frame[-1] ^ 1,))
        with self.assertRaises(ContractError):
            decode_artifact(damaged)
        with self.assertRaisesRegex(ContractError, "exceeds"):
            encode_artifact(job_id="job", artifact_id="a", role="x", mime_type="x", payload=b"1234", max_frame_bytes=8)

    def test_job_rejects_paths_and_commands(self) -> None:
        raw = JobEnvelope(
            job_id="j", request_id="r", capture_id="c", robot_id="robot", worker_id="w",
            kind="depth.infer", model_id="depth",
        ).as_dict()
        raw["payload"] = {"file_path": "/shared/image.jpg"}
        with self.assertRaisesRegex(ContractError, "not allowed"):
            JobEnvelope.from_dict(raw)

    def test_explicit_capability_routing(self) -> None:
        registry = WorkerRegistry((ModelRoute("model", "worker-a", "depth.infer"),))
        with self.assertRaisesRegex(ContractError, "not ready"):
            registry.route("model", "depth.infer")
        registry.update(WorkerStatus(
            "worker-a", "depth", "host", "cpu", True, False, ("depth.infer",),
            ({"model_id": "model"},), (), (DEPTH_SCHEMA,),
        ))
        self.assertEqual(registry.route("model", "depth.infer").worker_id, "worker-a")
        with self.assertRaisesRegex(ContractError, "no worker"):
            registry.route("unconfigured", "depth.infer")


class WorkerRuntimeTests(unittest.TestCase):
    def test_health_probe_refreshes_status_without_inference(self) -> None:
        transport, handler = FakeTransport(), EchoHandler()
        worker = MQTTWorker(worker_id="worker", worker_kind="test", transport=transport, handlers={"echo": handler})
        probe = JobEnvelope(
            job_id="health-1", request_id="request", capture_id="health", robot_id="robot",
            worker_id="worker", kind="job.status", model_id="echo", payload={"probe": True},
        )
        worker.handle_message(worker.topics.request("worker"), json.dumps(probe.as_dict()).encode())
        completed = [
            payload for topic, payload, _, _ in transport.published
            if topic.endswith("/event") and isinstance(payload, dict) and payload.get("job_id") == "health-1"
        ]
        self.assertEqual(handler.calls, 0)
        self.assertTrue(completed[-1]["payload"]["healthy"])

    def test_duplicate_job_runs_once_and_replays_terminal(self) -> None:
        transport, handler = FakeTransport(), EchoHandler()
        worker = MQTTWorker(worker_id="worker", worker_kind="test", transport=transport, handlers={"echo": handler})
        worker.start()
        try:
            job = JobEnvelope(
                job_id="job-1", request_id="request", capture_id="capture", robot_id="robot",
                worker_id="worker", kind="zero_shot.infer", model_id="echo",
            )
            encoded = json.dumps(job.as_dict()).encode()
            worker.handle_message(worker.topics.request("worker"), encoded)
            deadline = time.monotonic() + 2
            while handler.calls < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            worker.handle_message(worker.topics.request("worker"), encoded)
            self.assertEqual(handler.calls, 1)
            completed = [payload for topic, payload, _, _ in transport.published if topic.endswith("/event") and isinstance(payload, dict) and payload.get("status") == "completed"]
            self.assertGreaterEqual(len(completed), 2)
        finally:
            worker.stop()

    def test_conflicting_duplicate_artifact_is_rejected(self) -> None:
        worker = MQTTWorker(worker_id="worker", worker_kind="test", transport=FakeTransport(), handlers={"echo": EchoHandler()})
        first = encode_artifact(job_id="future", artifact_id="same", role="image", mime_type="image/jpeg", payload=b"one")
        second = encode_artifact(job_id="future", artifact_id="same", role="image", mime_type="image/jpeg", payload=b"two")
        worker._receive_artifact(first)
        with self.assertRaisesRegex(ContractError, "conflicting"):
            worker._receive_artifact(second)


class DepthAndCalibrationTests(unittest.TestCase):
    def test_depth_array_accepts_2d_and_rejects_nonfinite(self) -> None:
        import numpy as np

        self.assertEqual(depth_array({"predicted_depth": np.ones((1, 2, 3))}).shape, (2, 3))
        with self.assertRaisesRegex(RuntimeError, "non-finite"):
            depth_array({"depth": np.asarray([[np.nan]])})

    def test_synthetic_nine_point_photo_is_repeatable(self) -> None:
        image = Image.new("RGB", (640, 480), "white")
        drawing = ImageDraw.Draw(image)
        for x in (180, 320, 460):
            for y in (120, 240, 360):
                drawing.ellipse((x - 14, y - 14, x + 14, y + 14), fill="black")
        output = BytesIO()
        image.save(output, "JPEG", quality=95)
        first, second = calibrate(output.getvalue()), calibrate(output.getvalue())
        self.assertEqual(first.points, second.points)
        self.assertEqual(len(first.points), 9)
        self.assertEqual(len(first.grid["zones"]), 288)


class IKTests(unittest.TestCase):
    def test_payload_shape_and_exact_ack_sequence(self) -> None:
        published = []
        ik = IKControl(lambda topic, payload, qos: published.append((topic, dict(payload), qos)) or True, sender="visual_ai")
        target = IKTargetV1(10, 75, 12, "test")
        first = ik.execute(topic="robot/test", operation_id="op", target=target, grab=True, telemetry=True)
        self.assertEqual(first.payload["action"], "baseRotate")
        self.assertEqual(first.payload["controlType"], "DEGREES")
        self.assertEqual(first.payload["sender"], "visual_ai")
        self.assertFalse(ik.handle_firmware("robot/test", {"sender": "firmware", "action_id": "wrong", "status": "completed"}))
        self.assertTrue(ik.handle_firmware("robot/test", {"sender": "firmware", "action_id": first.action_id, "status": "completed"}))
        self.assertEqual(published[1][1]["action"], "controlik")
        self.assertEqual(published[1][1]["distance"], 75)
        self.assertEqual(published[1][1]["z_height"], 12)

    def test_bounds_reject_before_publish(self) -> None:
        published = []
        ik = IKControl(lambda *args: published.append(args) or True)
        result = ik.execute(topic="robot/test", operation_id="op", target=IKTargetV1(90, 50, 0, "bad"))
        self.assertFalse(result.success)
        self.assertEqual(published, [])

    def test_learned_provider_mismatch_is_rejected_without_fallback(self) -> None:
        compatibility = LearnedModelCompatibility("model", "other-detector", "abc", "depth", "def")
        prediction = IKPredictionV1(1, 2, 3, "model", "1")
        target = target_from_prediction(observation(), prediction, compatibility)
        self.assertFalse(target.accepted)
        self.assertIn("detector_model_mismatch", target.rejection_reasons)


class ACLTests(unittest.TestCase):
    def test_worker_acl_has_only_its_directed_topics(self) -> None:
        from ..network import accounts
        from ..network import broker_commands

        with tempfile.TemporaryDirectory() as temporary:
            acl = Path(temporary) / "acl"
            with patch.object(broker_commands, "acl_path", return_value=acl):
                accounts._write_acl([accounts.Account(
                    "depth-service", vision_worker_id="depth-hf-1",
                    managed_identity="worker:depth-hf-1",
                )])
            text = acl.read_text()
        self.assertIn("# managed-identity: worker:depth-hf-1", text)
        self.assertIn("topic read desk_buddy/vision/worker/depth-hf-1/request", text)
        self.assertIn("topic write desk_buddy/vision/worker/depth-hf-1/status", text)
        self.assertNotIn("topic readwrite #", text)
        self.assertNotIn("robot/test", text)


class StorageAndDatasetTests(unittest.TestCase):
    def test_inference_worker_keeps_only_the_latest_loaded_model(self) -> None:
        handler = IKInferenceHandler()
        callbacks = []
        handler.set_status_callback(lambda: callbacks.append(True))
        handler._replace_loaded_model("first", object(), {"model_version": "1"})
        handler._replace_loaded_model("second", object(), {"model_version": "2"})
        self.assertEqual(tuple(handler._models), ("second",))
        self.assertEqual(len(callbacks), 2)

    def test_only_reviewed_successes_enter_dataset_and_model_is_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = VisionStore(temporary)
            for index in range(3):
                capture_id = f"capture-{index}"
                store.artifacts.put(f"original-{index}", jpeg(), ".jpg")
                store.begin_capture(
                    capture_id=capture_id, request_id=f"request-{index}", robot_id="robot",
                    prompt="cup", original_artifact_id=f"original-{index}",
                )
                store.update_capture(capture_id, observation_json=observation(capture_id).as_dict())
            store.review("capture-0", disposition="successful", rotation_deg=1, distance_mm=2, z_height_mm=3)
            store.review("capture-1", disposition="successful", rotation_deg=4, distance_mm=5, z_height_mm=6)
            store.review("capture-2", disposition="failed")
            examples = store.training_examples("robot")
            self.assertEqual(len(examples), 2)
            bundle, metadata = build_dataset_bundle(examples)
            unpacked = read_dataset_bundle(bundle)
            self.assertEqual(metadata["example_count"], 2)
            self.assertEqual(set(unpacked["capture_ids"].tolist()), {"capture-0", "capture-1"})
            store.artifacts.put("checkpoint", b"model", ".pt")
            store.save_model(model_id="m", model_version="1", robot_id="robot", artifact_id="checkpoint", metadata={})
            self.assertFalse(store.model("m")["active"])


class ControllerCorrelationTests(unittest.TestCase):
    def test_selected_provider_routes_the_next_job_to_that_exact_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            transport = FakeTransport()
            config = ControllerConfig(
                robot_id="robot", robot_topic="robot", detector_model_id="detector-a", depth_model_id="depth",
                detector_worker_id="detector-worker-a", depth_worker_id="depth-worker",
            )
            controller = VisionController(config=config, transport=transport, store=VisionStore(temporary))
            for status in (
                WorkerStatus(
                    "detector-worker-a", "detector", "h", "cpu", True, False,
                    ("zero_shot.infer",), ({"model_id": "detector-a"},), (), (),
                ),
                WorkerStatus(
                    "detector-worker-b", "detector", "h", "cpu", True, False,
                    ("zero_shot.infer",), ({"model_id": "detector-b"},), (), (),
                ),
                WorkerStatus(
                    "depth-worker", "depth", "h", "cpu", True, False,
                    ("depth.infer",), ({"model_id": "depth"},), (), (),
                ),
            ):
                controller.registry.update(status)

            controller.select_provider("detector", "detector-b", "detector-worker-b")
            capture_id = controller.process_image(jpeg(), prompt="cup")
            context = controller.context(capture_id)
            detector_job_id = context.jobs["zero_shot.infer"]
            request_messages = [
                (topic, payload)
                for topic, payload, qos, retained in transport.published
                if topic.endswith("/request") and isinstance(payload, dict)
            ]
            detector_topic, detector_job = next(
                (topic, payload) for topic, payload in request_messages if payload["job_id"] == detector_job_id
            )
            self.assertEqual(detector_topic, controller.topics.request("detector-worker-b"))
            self.assertEqual(detector_job["model_id"], "detector-b")

    def test_deterministic_capture_requires_only_detector_without_motion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            transport = FakeTransport()
            config = ControllerConfig(
                robot_id="robot", robot_topic="robot", detector_model_id="detector", depth_model_id="depth",
                detector_worker_id="detector-worker", depth_worker_id="depth-worker",
            )
            controller = VisionController(config=config, transport=transport, store=VisionStore(temporary))
            controller.set_calibration_grid("robot", calibration_grid())
            status = WorkerStatus(
                "detector-worker", "detector", "h", "cpu", True, False,
                ("zero_shot.infer",), ({"model_id": "detector"},), (), (),
            )
            controller.handle_message(controller.topics.status(status.worker_id), json.dumps(status.as_dict()).encode())
            capture_id = controller.process_image(jpeg(), prompt="cup")
            context = controller.context(capture_id)
            self.assertEqual(set(context.jobs), {"zero_shot.infer"})
            detection = DetectionV1.create(
                label="cup", score=0.95, bbox_px=(50, 35, 110, 95), image_width=160, image_height=120,
                model_id="detector", model_version="abc",
            )
            batch = DetectionBatchV1(160, 120, "cup", (detection,), "detector", "abc")
            detection_job = context.jobs["zero_shot.infer"]
            controller.handle_message(controller.topics.event("detector-worker"), json.dumps(JobEvent(
                detection_job, context.request_id, capture_id, "robot", "detector-worker", "zero_shot.infer", "detector", "completed",
                {"detection_batch": batch.as_dict(), "timing_ms": 12.5}, model_version="abc",
            ).as_dict()).encode())
            self.assertEqual(context.state, "preview")
            self.assertIsNone(context.observation)
            self.assertEqual(context.selected_detection.label, "cup")
            firmware = [item for item in transport.published if item[0] == "robot/test"]
            self.assertEqual(firmware, [])

    def test_learned_capture_is_explicitly_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            transport = FakeTransport()
            config = ControllerConfig("robot", "robot", "detector", "depth", "dw", "zw")
            controller = VisionController(config=config, transport=transport, store=VisionStore(temporary))
            with self.assertRaisesRegex(ValueError, "not enabled"):
                controller.process_image(jpeg(), prompt="cup", planner="learned", learned_model_id="model")
            self.assertFalse(any(topic == "robot/test" for topic, *_ in transport.published))

    def test_training_loading_and_activation_are_explicitly_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            transport = FakeTransport()
            config = ControllerConfig("robot", "robot", "detector", "depth", "dw", "zw")
            controller = VisionController(config=config, transport=transport, store=VisionStore(temporary))
            for operation in (
                lambda: controller.start_training("model"),
                lambda: controller.load_model("model"),
                lambda: controller.set_active_model("model"),
            ):
                with self.assertRaisesRegex(RuntimeError, "not enabled"):
                    operation()
            self.assertEqual(transport.published, [])

    def test_mismatched_worker_response_fails_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            transport = FakeTransport()
            config = ControllerConfig("robot", "robot", "detector", "depth", "dw", "zw")
            controller = VisionController(config=config, transport=transport, store=VisionStore(temporary))
            controller.registry.update(WorkerStatus("dw", "d", "h", "cpu", True, False, ("zero_shot.infer",), ({"model_id": "detector"},), (), ()))
            controller.registry.update(WorkerStatus("zw", "z", "h", "cpu", True, False, ("depth.infer",), ({"model_id": "depth"},), (), ()))
            capture_id = controller.process_image(jpeg(), prompt="cup")
            context = controller.context(capture_id)
            event = JobEvent(
                context.jobs["zero_shot.infer"], "wrong-request", capture_id, "robot", "dw",
                "zero_shot.infer", "detector", "completed", {"detection_batch": {}},
            )
            controller.handle_message(controller.topics.event("dw"), json.dumps(event.as_dict()).encode())
            self.assertEqual(context.state, "failed")
            self.assertIn("correlation", context.error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
