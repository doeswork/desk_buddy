from __future__ import annotations

from typing import Sequence

import cv2
import numpy as np

from desk_buddy_vision_protocol import Detection, FeatureSet

from .planning import CalibrationProjection, MotionTarget


def select_detection(detections: Sequence[Detection]) -> Detection | None:
    if not detections:
        return None
    return max(detections, key=lambda detection: detection.score)


def _depth_crop(depth: np.ndarray, bbox_norm: Sequence[float]) -> np.ndarray:
    if depth.ndim != 2 or depth.size == 0:
        raise ValueError("normalized depth map must be a non-empty 2D array")
    if len(bbox_norm) != 4:
        raise ValueError("bbox_norm must contain four values")
    height, width = depth.shape
    x0, y0, x1, y1 = (min(max(float(value), 0.0), 1.0) for value in bbox_norm)
    left = min(width - 1, max(0, int(np.floor(min(x0, x1) * width))))
    right = min(width, max(left + 1, int(np.ceil(max(x0, x1) * width))))
    top = min(height - 1, max(0, int(np.floor(min(y0, y1) * height))))
    bottom = min(height, max(top + 1, int(np.ceil(max(y0, y1) * height))))
    return depth[top:bottom, left:right]


def build_feature_set(
    *,
    image_width: int,
    image_height: int,
    phrase: str,
    detection: Detection,
    normalized_depth: np.ndarray,
    depth_near_is_high: bool,
    projection: CalibrationProjection,
    baseline: MotionTarget,
) -> FeatureSet:
    values = np.asarray(normalized_depth, dtype=np.float32)
    if not np.all(np.isfinite(values)):
        raise ValueError("normalized depth map contains non-finite values")
    values = np.clip(values, 0.0, 1.0)
    # features.v1 always uses 1.0 for nearer values. A worker can use the
    # opposite native convention, but that must not leak into training data.
    if not depth_near_is_high:
        values = 1.0 - values
    crop = _depth_crop(values, detection.bbox_norm)
    patch = cv2.resize(crop, (64, 64), interpolation=cv2.INTER_AREA).astype(np.float32)
    flat = patch.reshape(-1)
    percentiles = tuple(float(value) for value in np.percentile(flat, [5, 25, 50, 75, 95]))
    return FeatureSet(
        image_width=int(image_width),
        image_height=int(image_height),
        phrase=str(phrase),
        selected_label=detection.label,
        detection_score=float(detection.score),
        bbox_norm=detection.bbox_norm,
        bbox_center_norm=detection.center_norm,
        bbox_area_norm=float(detection.area_norm),
        depth_patch_64x64=tuple(float(value) for value in flat),
        depth_mean=float(np.mean(flat)),
        depth_median=float(np.median(flat)),
        depth_std=float(np.std(flat)),
        depth_min=float(np.min(flat)),
        depth_max=float(np.max(flat)),
        depth_percentiles=percentiles,
        calibration_angle_deg=float(projection.angle_deg),
        calibration_distance_mm=float(projection.distance_mm),
        calibration_z_height_mm=float(projection.z_height_mm),
        calibration_zone=projection.zone,
        calibration_extrapolated=projection.extrapolated,
        baseline_rotation_deg=float(baseline.rotation_deg),
        baseline_controlik_distance_mm=float(baseline.distance_mm),
        baseline_controlik_z_height_mm=float(baseline.z_height_mm),
    )


def annotate_detection(jpeg: bytes, detection: Detection) -> bytes:
    image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("failed to decode detection JPEG")
    height, width = image.shape[:2]
    x0, y0, x1, y1 = detection.bbox_px
    left = max(0, min(width - 1, int(round(x0))))
    right = max(0, min(width - 1, int(round(x1))))
    top = max(0, min(height - 1, int(round(y0))))
    bottom = max(0, min(height - 1, int(round(y1))))
    cv2.rectangle(image, (left, top), (right, bottom), (0, 200, 0), 2)
    label = f"{detection.label} {detection.score:.2f}"
    cv2.putText(image, label, (left, max(15, top - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 0), 1)
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
        raise RuntimeError("failed to encode detection annotation")
    return encoded.tobytes()
