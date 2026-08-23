from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.support import FakeBroker, wait_until
from tests.test_residual_model import features

from desk_buddy_planner_training.handler import ResidualPlannerHandler
from desk_buddy_vision_protocol import MQTTWorker, TopicLayout, VisionRequest
from desk_buddy_vision.config import AppConfig, CoordinatorConfig, ModelRoute, MQTTConfig, RobotConfig
from desk_buddy_vision.coordinator import VisionCoordinator


class TrainingIntegrationTests(unittest.TestCase):
    def test_train_register_activate_and_reload_over_mqtt_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            broker = FakeBroker()
            root = "training_test"
            config = AppConfig(
                mqtt=MQTTConfig(host="fake", tls=False, topic_root=root),
                coordinator=CoordinatorConfig(
                    service_id="coordinator",
                    data_dir=Path(temporary),
                    worker_timeout_seconds=10,
                ),
                robots=(RobotConfig("robot-1", f"{root}/robot-1"),),
                models=(ModelRoute("ik-residual-v1", "planner", "planner-service", True),),
            )
            coordinator_transport = broker.client("coordinator")
            coordinator = VisionCoordinator(config=config, transport=coordinator_transport)
            coordinator_transport.set_message_handler(coordinator.handle_message)
            coordinator.start()

            handler = ResidualPlannerHandler(model_id="ik-residual-v1")
            planner_transport = broker.client("planner-remote-host")
            planner = MQTTWorker(
                service_id="planner-service",
                service_kind="planner_training",
                transport=planner_transport,
                handlers={"ik-residual-v1": handler},
                topics=TopicLayout(root),
            )
            planner_transport.set_message_handler(planner.handle_message)
            planner.start()
            gui = broker.client("gui")
            try:
                wait_until(lambda: "planner-service" in coordinator._capabilities)
                for index in range(5):
                    source_request = VisionRequest(
                        request_id=f"source-{index}",
                        robot_id="robot-1",
                        sender="test-seed",
                        kind="detect",
                        payload={"execute": False},
                    )
                    operation, _ = coordinator.storage.create_operation(source_request)
                    snapshot = coordinator.artifacts.save_bytes(
                        operation_id=operation.id,
                        robot_id="robot-1",
                        kind="features",
                        payload=json.dumps({"features": features(index / 10)}).encode("utf-8"),
                        mime_type="application/json",
                    )
                    coordinator.storage.upsert_training_example(
                        operation_id=operation.id,
                        feature_snapshot_artifact_id=snapshot.artifact_id,
                        success=True,
                        reviewed=True,
                        eligible=True,
                        target_rotation_delta_deg=float(index),
                        target_distance_delta_mm=float(index * 2),
                        target_z_delta_mm=float(-index),
                    )
                    coordinator.storage.update_operation(operation.id, status="completed", stage="test_seed")

                training = VisionRequest(
                    request_id="training-1",
                    robot_id="robot-1",
                    sender="gui",
                    kind="training_start",
                    payload={
                        "planner_model": "ik-residual-v1",
                        "planner_model_name": "integration",
                        "feature_schema_version": "features.v1",
                        "filters": {"reviewed_only": True, "success_only": True},
                        "validation_split": 0.2,
                        "seed": 42,
                    },
                )
                gui.publish(TopicLayout(root).vision_request("robot-1"), training.as_dict(), qos=1)
                trained = wait_until(lambda: self._terminal_event(broker, "training-1"), timeout=15)
                self.assertEqual(trained["status"], "completed")
                self.assertTrue(trained["payload"]["activation_required"])
                model_id = trained["payload"]["model_id"]
                version = trained["payload"]["model_version"]
                stored = coordinator.storage.get_model(model_id, version)
                self.assertFalse(stored["is_active"])
                self.assertIn("model_weights", stored["metadata"]["artifact_ids"])

                activation = VisionRequest(
                    request_id="activation-1",
                    robot_id="robot-1",
                    sender="gui",
                    kind="model_activate",
                    payload={"model_id": model_id, "version": version},
                )
                gui.publish(TopicLayout(root).vision_request("robot-1"), activation.as_dict(), qos=1)
                activated = wait_until(lambda: self._terminal_event(broker, "activation-1"), timeout=15)
                self.assertEqual(activated["status"], "completed")
                self.assertTrue(coordinator.storage.get_model(model_id, version)["is_active"])
                self.assertEqual(handler.model_version, version)
            finally:
                planner.stop()
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

