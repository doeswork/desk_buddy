"""Canonical, provider-independent features used by custom IK models."""

from __future__ import annotations

from typing import Iterable

from ..contracts import FEATURE_SCHEMA, VisionObservationV1

GEOMETRY_FEATURES = (
    "box_left", "box_top", "box_right", "box_bottom", "box_center_x",
    "box_center_y", "box_width", "box_height", "box_area", "detector_score",
)
POOL_SIZE = 8
ROTATION_INPUT_SIZE = len(GEOMETRY_FEATURES)
CONTROL_INPUT_SIZE = ROTATION_INPUT_SIZE + POOL_SIZE * POOL_SIZE


def geometry_features(observation: VisionObservationV1) -> tuple[float, ...]:
    left, top, right, bottom = observation.detection.bbox_norm
    center_x, center_y = observation.detection.center_norm
    return (
        left, top, right, bottom, center_x, center_y,
        max(0.0, right - left), max(0.0, bottom - top),
        observation.detection.area_norm, observation.detection.score,
    )


def pooled_depth(values: Iterable[float]) -> tuple[float, ...]:
    """Average-pool a canonical 64x64 crop to 8x8 without image libraries."""
    patch = tuple(float(value) for value in values)
    if len(patch) != 64 * 64:
        raise ValueError("canonical depth crop must contain 4096 values")
    pooled = []
    stride = 64 // POOL_SIZE
    for pool_y in range(POOL_SIZE):
        for pool_x in range(POOL_SIZE):
            total = 0.0
            for y in range(pool_y * stride, (pool_y + 1) * stride):
                offset = y * 64 + pool_x * stride
                total += sum(patch[offset : offset + stride])
            pooled.append(total / float(stride * stride))
    return tuple(pooled)


def feature_rows(observation: VisionObservationV1) -> tuple[tuple[float, ...], tuple[float, ...]]:
    geometry = geometry_features(observation)
    return geometry, geometry + pooled_depth(observation.depth_patch_64x64)


def provider_signature(observation: VisionObservationV1) -> dict[str, str]:
    return {
        "feature_schema": FEATURE_SCHEMA,
        "detector_model_id": observation.detector_model_id,
        "detector_model_version": observation.detector_model_version,
        "depth_model_id": observation.depth_model_id,
        "depth_model_version": observation.depth_model_version,
    }
