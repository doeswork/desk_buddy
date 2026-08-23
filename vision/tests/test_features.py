from __future__ import annotations

import unittest

import numpy as np

from tests.support import ROOT

from desk_buddy_vision.features import build_feature_set
from desk_buddy_vision.planning import CalibrationProjection, MotionTarget
from desk_buddy_vision_protocol import Detection


class FeatureTests(unittest.TestCase):
    def test_depth_direction_is_canonicalized_for_features_v1(self):
        detection = Detection.create(
            label="block",
            score=0.9,
            bbox_px=(0, 0, 4, 4),
            image_width=4,
            image_height=4,
            source_model_id="fake",
            source_model_version="1",
        )
        depth = np.full((4, 4), 0.2, dtype=np.float32)
        common = {
            "image_width": 4,
            "image_height": 4,
            "phrase": "block",
            "detection": detection,
            "projection": CalibrationProjection(0.0, 60.0, 0.0, "center", True, False),
            "baseline": MotionTarget(0.0, 60.0, 0.0),
        }

        high_means_near = build_feature_set(
            **common,
            normalized_depth=depth,
            depth_near_is_high=True,
        )
        low_means_near = build_feature_set(
            **common,
            normalized_depth=depth,
            depth_near_is_high=False,
        )

        self.assertAlmostEqual(high_means_near.depth_mean, 0.2, places=6)
        self.assertAlmostEqual(low_means_near.depth_mean, 0.8, places=6)


if __name__ == "__main__":
    unittest.main()
