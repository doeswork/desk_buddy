from __future__ import annotations

import unittest

from tests.support import FakeBroker

from desk_buddy_vision_client import VisionMQTTClient
from desk_buddy_vision_protocol import TopicLayout, encode_binary_frame
from desk_buddy_vision_protocol.artifacts import sha256_bytes


class GUIClientTests(unittest.TestCase):
    def test_client_subscribes_only_to_coordinator_topics_and_reassembles_artifact(self):
        broker = FakeBroker()
        transport = broker.client("gui")
        client = VisionMQTTClient(
            robot_id="robot-1",
            sender="gui",
            transport=transport,
            topics=TopicLayout("test"),
        )
        client.start()
        self.assertEqual(
            set(transport.subscriptions),
            {
                "test/robot-1/vision/event",
                "test/robot-1/vision/artifact/chunk",
                "test/robot-1/vision/status",
                "test/vision/coordinator/status",
            },
        )
        statuses = []
        client.add_status_callback(statuses.append)
        coordinator = broker.client("coordinator")
        coordinator.publish(
            "test/vision/coordinator/status",
            {"schema": "desk_buddy.vision.v1", "status": "online"},
            qos=1,
            retain=True,
        )
        self.assertEqual(client.coordinator_status["status"], "online")
        self.assertEqual(statuses[-1]["status"], "online")
        request_id = client.request_artifact("artifact-1", request_id="artifact-request")
        payload = b"artifact bytes"
        coordinator.publish(
            "test/robot-1/vision/event",
            {
                "schema": "desk_buddy.vision.v1",
                "request_id": request_id,
                "operation_id": "operation-1",
                "sender": "vision_coordinator",
                "kind": "operation.event",
                "status": "processing",
                "stage": "artifact_transfer",
                "payload": {
                    "kind": "artifact.metadata",
                    "artifact_id": "artifact-1",
                    "mime_type": "application/octet-stream",
                    "byte_size": len(payload),
                    "sha256": sha256_bytes(payload),
                    "chunk_count": 1,
                },
                "error": None,
            },
            qos=1,
        )
        coordinator.publish(
            "test/robot-1/vision/artifact/chunk",
            encode_binary_frame(
                {
                    "request_id": request_id,
                    "artifact_id": "artifact-1",
                    "chunk_index": 0,
                    "chunk_count": 1,
                },
                payload,
            ),
            qos=1,
        )
        result = client.wait_artifact(request_id, timeout=1)
        self.assertEqual(result.payload, payload)


if __name__ == "__main__":
    unittest.main()
