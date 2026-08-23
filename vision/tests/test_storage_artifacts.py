from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.support import ROOT

from desk_buddy_vision_protocol import VisionRequest
from desk_buddy_vision.artifacts import ArtifactStore, ArtifactUploadManager
from desk_buddy_vision.storage import VisionStorage


def request(request_id: str = "request-1") -> VisionRequest:
    return VisionRequest(
        request_id=request_id,
        robot_id="robot-1",
        sender="gui",
        kind="photo",
    )


class StorageArtifactTests(unittest.TestCase):
    def test_operation_idempotency_and_artifact_integrity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            storage = VisionStorage(root / "vision.sqlite3")
            operation, created = storage.create_operation(request())
            duplicate, duplicate_created = storage.create_operation(request())
            self.assertTrue(created)
            self.assertFalse(duplicate_created)
            self.assertEqual(operation.id, duplicate.id)
            store = ArtifactStore(root / "artifacts", storage)
            record = store.save_bytes(
                operation_id=operation.id,
                robot_id="robot-1",
                kind="original",
                payload=b"\xff\xd8hello\xff\xd9",
                mime_type="image/jpeg",
            )
            loaded, payload = store.read_bytes(record.artifact_id)
            self.assertEqual(payload, b"\xff\xd8hello\xff\xd9")
            self.assertEqual(loaded.sha256, record.sha256)
            storage.close()

    def test_worker_upload_duplicate_chunk_and_hash_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            storage = VisionStorage(root / "vision.sqlite3")
            operation, _ = storage.create_operation(request())
            manager = ArtifactUploadManager(ArtifactStore(root / "artifacts", storage))
            payload = b"worker artifact"
            import hashlib

            session = manager.begin(
                {
                    "transfer_id": "transfer-1",
                    "service_id": "worker-1",
                    "job_id": "job-1",
                    "operation_id": operation.id,
                    "robot_id": "robot-1",
                    "artifact_id": "artifact-1",
                    "kind": "features",
                    "mime_type": "application/json",
                    "byte_size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "chunk_count": 1,
                    "chunk_size": 4096,
                }
            )
            with self.assertRaises(PermissionError):
                manager.add_chunk(
                    session.transfer_id,
                    0,
                    payload,
                    service_id="different-worker",
                )
            self.assertTrue(manager.add_chunk(session.transfer_id, 0, payload))
            self.assertFalse(manager.add_chunk(session.transfer_id, 0, payload))
            record = manager.finish(session.transfer_id)
            self.assertEqual(record.artifact_id, "artifact-1")
            storage.close()

    def test_restart_recovery_marks_uncertain_work_failed(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage = VisionStorage(Path(temporary) / "vision.sqlite3")
            operation, _ = storage.create_operation(request())
            storage.update_operation(operation.id, status="waiting_for_photo", stage="firmware_photo")
            self.assertEqual(storage.recover_incomplete_operations(), 1)
            recovered = storage.get_operation(operation.id)
            self.assertEqual(recovered.status, "failed")
            self.assertEqual(recovered.error_code, "coordinator_restarted")

            accepted, _ = storage.create_operation(request("accepted-before-crash"))
            self.assertEqual(storage.recover_incomplete_operations(), 1)
            self.assertEqual(storage.get_operation(accepted.id).status, "failed")

    def test_terminal_operation_result_is_persisted_for_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage = VisionStorage(Path(temporary) / "vision.sqlite3")
            vision_request = request("persisted-result")
            operation, _ = storage.create_operation(vision_request)
            result = {"artifact_id": "artifact-1", "target_found": True}
            storage.update_operation(operation.id, status="completed", stage="completed", result=result)

            recovered = storage.get_operation_by_request(vision_request.request_id)
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered.result, result)
            self.assertEqual(storage.list_operations(robot_id=vision_request.robot_id)[0].id, operation.id)
            storage.close()


if __name__ == "__main__":
    unittest.main()
