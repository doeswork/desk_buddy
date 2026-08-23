from __future__ import annotations

import unittest

import numpy as np

from tests.support import ROOT

from desk_buddy_depth_hf.adapter import _array_from_output
from desk_buddy_detector_hf.adapter import HuggingFaceZeroShotHandler


class _Image:
    width = 640
    height = 480


class _ObjectProcessor:
    def post_process_object_detection(self, outputs, *, target_sizes, threshold):
        return [{"labels": [0], "scores": [0.9], "boxes": [[1, 2, 3, 4]]}]


class _DinoProcessor:
    def post_process_grounded_object_detection(
        self, outputs, input_ids, *, target_sizes, box_threshold, text_threshold
    ):
        return [{"text_labels": ["block"], "scores": [0.8], "boxes": [[1, 2, 3, 4]]}]


class _OmDetProcessor:
    def post_process_grounded_object_detection(
        self, outputs, *, classes, target_sizes, score_threshold, nms_threshold
    ):
        return [{"classes": classes, "scores": [0.7], "boxes": [[1, 2, 3, 4]]}]


class ModelAdapterTests(unittest.TestCase):
    def setUp(self):
        self.handler = HuggingFaceZeroShotHandler(
            model_id="fake",
            source="fake",
            model_version="1",
        )

    def test_detector_post_processing_families(self):
        cases = (
            (_ObjectProcessor(), {}, "labels"),
            (_DinoProcessor(), {"input_ids": [1]}, "text_labels"),
            (_OmDetProcessor(), {}, "classes"),
        )
        for processor, inputs, expected_key in cases:
            with self.subTest(processor=type(processor).__name__):
                self.handler._processor = processor
                result = self.handler._post_process(
                    outputs={},
                    inputs=inputs,
                    image=_Image(),
                    phrases=["block"],
                    box_threshold=0.25,
                    text_threshold=0.2,
                )
                self.assertIn(expected_key, result)

    def test_depth_output_normalizes_shape_and_dtype(self):
        result = _array_from_output({"predicted_depth": np.arange(12).reshape(1, 3, 4)})
        self.assertEqual(result.shape, (3, 4))
        self.assertEqual(result.dtype, np.float32)
        with self.assertRaisesRegex(RuntimeError, "non-finite"):
            _array_from_output({"depth": np.asarray([[np.nan]])})


if __name__ == "__main__":
    unittest.main()

