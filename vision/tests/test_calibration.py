from __future__ import annotations

import unittest

import cv2
import numpy as np

from tests.support import ROOT

from desk_buddy_vision.calibration import build_calibration_grid, calibrate, project_detection


def synthetic_calibration_jpeg() -> bytes:
    image = np.full((480, 640, 3), 255, dtype=np.uint8)
    for x in (180, 320, 460):
        for y in (120, 240, 360):
            cv2.circle(image, (x, y), 14, (0, 0, 0), -1, cv2.LINE_AA)
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    assert ok
    return encoded.tobytes()


class CalibrationTests(unittest.TestCase):
    def test_synthetic_nine_point_calibration_is_repeatable(self):
        jpeg = synthetic_calibration_jpeg()
        first = calibrate(jpeg)
        second = calibrate(jpeg)
        self.assertEqual(first.points, second.points)
        self.assertEqual(first.grid, second.grid)
        self.assertEqual(len(first.points), 9)
        self.assertTrue(first.annotated_jpeg.startswith(b"\xff\xd8"))

    def test_projection_distinguishes_calibrated_and_rejected(self):
        result = calibrate(synthetic_calibration_jpeg())
        inside = project_detection((300, 220, 340, 260), result.grid, reference_y_fraction=0.5)
        outside = project_detection((0, 0, 20, 20), result.grid, reference_y_fraction=0.5)
        self.assertTrue(inside.calibrated)
        self.assertFalse(outside.calibrated)
        extrapolated = project_detection(
            (0, 0, 20, 20), result.grid, reference_y_fraction=0.5, allow_extrapolation=True
        )
        self.assertTrue(extrapolated.extrapolated)

    def test_missing_point_fails(self):
        image = np.full((480, 640, 3), 255, dtype=np.uint8)
        for x in (180, 320, 460):
            for y in (120, 240, 360):
                if (x, y) != (460, 360):
                    cv2.circle(image, (x, y), 14, (0, 0, 0), -1)
        ok, encoded = cv2.imencode(".jpg", image)
        self.assertTrue(ok)
        with self.assertRaisesRegex(ValueError, "expected exactly 9"):
            calibrate(encoded.tobytes())


if __name__ == "__main__":
    unittest.main()

