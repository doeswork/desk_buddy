"""Pure nine-point image-to-workspace calibration and projection."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

COLUMNS = ("left", "center", "right")
DEPTHS = (0, 60, 120)


@dataclass(frozen=True)
class CalibrationProjection:
    angle_deg: float
    distance_mm: float
    z_height_mm: float
    zone: str
    calibrated: bool
    extrapolated: bool = False


def _quadratic(t: float, p0: float, p1: float, p2: float) -> float:
    return (
        2.0 * (t - 0.5) * (t - 1.0) * p0
        - 4.0 * t * (t - 1.0) * p1
        + 2.0 * t * (t - 0.5) * p2
    )


def _interpolator(points: Mapping[str, Mapping[str, float]]):
    required = [f"{column}_{depth}" for column in COLUMNS for depth in DEPTHS]
    missing = [key for key in required if key not in points]
    if missing:
        raise ValueError(f"missing calibration points: {', '.join(missing)}")
    values = [
        [
            (float(points[f"{column}_{depth}"]["x"]), float(points[f"{column}_{depth}"]["y"]))
            for column in COLUMNS
        ]
        for depth in DEPTHS
    ]

    def at(angle_deg: float, depth_mm: float) -> tuple[float, float]:
        horizontal = (float(angle_deg) + 30.0) / 60.0
        vertical = float(depth_mm) / 120.0
        columns = []
        for index in range(3):
            p0, p1, p2 = (values[row][index] for row in range(3))
            columns.append(
                (
                    _quadratic(vertical, p0[0], p1[0], p2[0]),
                    _quadratic(vertical, p0[1], p1[1], p2[1]),
                )
            )
        return (
            _quadratic(horizontal, columns[0][0], columns[1][0], columns[2][0]),
            _quadratic(horizontal, columns[0][1], columns[1][1], columns[2][1]),
        )

    return at


def _polygon_area(points: Sequence[Sequence[float]]) -> float:
    return abs(
        sum(
            float(points[index][0]) * float(points[(index + 1) % len(points)][1])
            - float(points[(index + 1) % len(points)][0]) * float(points[index][1])
            for index in range(len(points))
        )
        / 2.0
    )


def build_grid(
    points: Mapping[str, Mapping[str, float]],
    *,
    angle_step: int = 5,
    distance_step: int = 5,
) -> dict[str, Any]:
    if angle_step <= 0 or distance_step <= 0 or 60 % angle_step or 120 % distance_step:
        raise ValueError("grid steps must evenly divide 60 degrees and 120 millimeters")
    at = _interpolator(points)
    angles = list(range(-30, 31, angle_step))
    distances = list(range(0, 121, distance_step))
    zones = []
    for angle_index, (angle_min, angle_max) in enumerate(zip(angles, angles[1:])):
        for distance_index, (distance_min, distance_max) in enumerate(zip(distances, distances[1:])):
            polygon = [
                list(at(angle_min, distance_min)),
                list(at(angle_max, distance_min)),
                list(at(angle_max, distance_max)),
                list(at(angle_min, distance_max)),
            ]
            if _polygon_area(polygon) < 0.5:
                raise ValueError("calibration produced a degenerate grid zone")
            xs = [point[0] for point in polygon]
            ys = [point[1] for point in polygon]
            if angle_max <= 0:
                angle_value = float(angle_min)
            elif angle_min >= 0:
                angle_value = float(angle_max)
            else:
                angle_value = 0.0
            zones.append(
                {
                    "id": f"a{angle_index}-d{distance_index}",
                    "angle_min": float(angle_min),
                    "angle_max": float(angle_max),
                    "distance_min": float(distance_min),
                    "distance_max": float(distance_max),
                    "angle_value": angle_value,
                    "distance_value": float(distance_max),
                    "polygon": polygon,
                    "bbox": [min(xs), min(ys), max(xs), max(ys)],
                }
            )
    return {
        "schema": "nine-point-grid.v1",
        "angle_step": angle_step,
        "distance_step": distance_step,
        "angle_lines": angles,
        "distance_lines": distances,
        "zones": zones,
    }


def _contains(x: float, y: float, polygon: Sequence[Sequence[float]]) -> bool:
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = float(previous[0]), float(previous[1])
        x2, y2 = float(current[0]), float(current[1])
        if min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2):
            cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
            if abs(cross) < 1e-6:
                return True
        crosses = (y1 > y) != (y2 > y)
        if crosses and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
        previous = current
    return inside


def find_zone(x: float, y: float, grid: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for zone in grid.get("zones", []):
        left, top, right, bottom = zone["bbox"]
        if left <= x <= right and top <= y <= bottom and _contains(x, y, zone["polygon"]):
            return zone
    return None


def reference_point(bbox_px: Sequence[float], reference_y_fraction: float = 0.1) -> tuple[float, float]:
    if len(bbox_px) != 4:
        raise ValueError("bbox_px must contain four values")
    x0, y0, x1, y1 = (float(value) for value in bbox_px)
    x_low, x_high = sorted((x0, x1))
    y_low, y_high = sorted((y0, y1))
    fraction = min(max(float(reference_y_fraction), 0.0), 1.0)
    return (x_low + x_high) / 2.0, y_high - fraction * (y_high - y_low)


def project(
    bbox_px: Sequence[float],
    grid: Mapping[str, Any],
    *,
    reference_y_fraction: float = 0.1,
) -> CalibrationProjection:
    x, y = reference_point(bbox_px, reference_y_fraction)
    zone = find_zone(x, y, grid)
    if zone is None:
        return CalibrationProjection(0.0, 0.0, 0.0, "", False)
    return CalibrationProjection(
        angle_deg=float(zone["angle_value"]),
        distance_mm=float(zone["distance_value"]),
        z_height_mm=0.0,
        zone=str(zone["id"]),
        calibrated=True,
    )

