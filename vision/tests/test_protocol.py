from __future__ import annotations

import unittest

from tests.support import ROOT

from desk_buddy_vision_protocol import (
    ContractError,
    Detection,
    JobEnvelope,
    JobResult,
    TopicLayout,
    VisionRequest,
    decode_binary_frame,
    encode_binary_frame,
)
from desk_buddy_vision_protocol.artifacts import ChunkAssembler, iter_chunks, sha256_bytes


class ProtocolTests(unittest.TestCase):
    def test_vision_request_validation_and_forbidden_paths(self):
        valid = VisionRequest.from_mapping(
            {
                "schema": "desk_buddy.vision.v1",
                "request_id": "request-1",
                "robot_id": "robot-1",
                "sender": "gui",
                "kind": "detect",
                "created_at": "2026-08-22T20:00:00Z",
                "payload": {"phrase": "blue block"},
            }
        )
        self.assertEqual(valid.kind, "detect")
        with self.assertRaisesRegex(ContractError, "not accepted"):
            VisionRequest.from_mapping(valid.as_dict() | {"payload": {"model_path": "/tmp/model"}})

    def test_job_envelope_round_trip(self):
        job = JobEnvelope(
            request_id="request-1",
            operation_id="operation-1",
            job_id="job-1",
            service_id="detector-1",
            kind="detector.infer",
            model_id="owlv2-base",
            input_artifact_ids=("artifact-1",),
        )
        parsed = JobEnvelope.from_mapping(job.as_dict())
        self.assertEqual(parsed, job)

        invalid_result = {
            "schema": "desk_buddy.vision.job.v1",
            "request_id": "request-1",
            "operation_id": "operation-1",
            "job_id": "job-1",
            "service_id": "detector-1",
            "sender": "worker",
            "kind": "firmware.command",
            "model_id": "owlv2-base",
            "model_version": "1",
            "status": "completed",
            "payload": {},
            "error": None,
        }
        with self.assertRaisesRegex(ContractError, "unsupported job kind"):
            JobResult.from_mapping(invalid_result)

    def test_binary_frames_and_duplicate_chunks(self):
        payload = b"abcdefghij"
        frames = list(iter_chunks(payload, chunk_size=256))
        assembler = ChunkAssembler(chunk_count=1, byte_size=len(payload), sha256=sha256_bytes(payload))
        header = {"chunk_index": 0, "transfer_id": "t1"}
        decoded = decode_binary_frame(encode_binary_frame(header, frames[0][2]))
        self.assertEqual(decoded.header, header)
        self.assertTrue(assembler.add(0, decoded.payload))
        self.assertFalse(assembler.add(0, decoded.payload))
        self.assertEqual(assembler.finish(), payload)

    def test_detection_normalization_clips_to_image(self):
        detection = Detection.create(
            label="block",
            score=0.9,
            bbox_px=(-5, 10, 110, 90),
            image_width=100,
            image_height=100,
            source_model_id="fake",
            source_model_version="1",
        )
        self.assertEqual(detection.bbox_px, (0.0, 10.0, 100.0, 90.0))
        self.assertAlmostEqual(detection.area_norm, 0.8)

    def test_topics_keep_gui_and_internal_namespaces_distinct(self):
        topics = TopicLayout("test")
        self.assertEqual(topics.vision_request("r1"), "test/r1/vision/request")
        self.assertEqual(topics.coordinator_status(), "test/vision/coordinator/status")
        self.assertEqual(topics.service_request("detector"), "test/vision/service/detector/request")


if __name__ == "__main__":
    unittest.main()
