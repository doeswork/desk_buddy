from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import cv2
import numpy as np

from .planning import CalibrationProjection

COLUMNS = ("left", "center", "right")
DEPTHS = (0, 60, 120)


@dataclass(frozen=True)
class CalibrationResult:
    points: dict[str, dict[str, float]]
    grid: dict[str, Any]
    annotated_jpeg: bytes


def _decode_jpeg(jpeg: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("failed to decode calibration JPEG")
    return image


def _blob_score(contour: np.ndarray, gray: np.ndarray) -> tuple[float, float, float, float] | None:
    image_area = float(gray.shape[0] * gray.shape[1])
    area = float(cv2.contourArea(contour))
    if area < image_area * 0.0001 or area > image_area * 0.025:
        return None
    perimeter = float(cv2.arcLength(contour, True))
    if perimeter <= 0:
        return None
    circularity = 4.0 * np.pi * area / (perimeter * perimeter)
    if circularity < 0.6:
        return None
    moments = cv2.moments(contour)
    if moments["m00"] == 0:
        return None
    center_x = float(moments["m10"] / moments["m00"])
    center_y = float(moments["m01"] / moments["m00"])
    mask = np.zeros(gray.shape, dtype=np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, -1)
    mean_intensity = float(cv2.mean(gray, mask=mask)[0])
    if mean_intensity > 110:
        return None
    return center_x, center_y, area, circularity + (1.0 - mean_intensity / 255.0)


def detect_candidates(jpeg: bytes) -> list[tuple[float, float, float, float]]:
    image = _decode_jpeg(jpeg)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (9, 9), 2)
    threshold = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        45,
        10,
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    threshold = cv2.morphologyEx(threshold, cv2.MORPH_OPEN, kernel, iterations=1)
    found = cv2.findContours(threshold, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = found[0] if len(found) == 2 else found[1]
    candidates = [score for contour in contours if (score := _blob_score(contour, gray)) is not None]
    return sorted(candidates, key=lambda item: item[3], reverse=True)


def assign_points(candidates: Sequence[tuple[float, float, float, float]], *, y_down: bool = True) -> dict[str, dict[str, float]]:
    if len(candidates) != 9:
        raise ValueError(f"expected exactly 9 calibration circles, found {len(candidates)}")
    columns = [sorted(candidates, key=lambda item: item[0])[index : index + 3] for index in range(0, 9, 3)]
    points: dict[str, dict[str, float]] = {}
    column_means: list[float] = []
    for name, values in zip(COLUMNS, columns):
        column_means.append(sum(value[0] for value in values) / 3.0)
        rows = sorted(values, key=lambda item: item[1], reverse=y_down)
        if min(abs(rows[index][1] - rows[index + 1][1]) for index in range(2)) < 2.0:
            raise ValueError(f"degenerate calibration column: {name}")
        for depth, value in zip(DEPTHS, rows):
            points[f"{name}_{depth}"] = {"x": float(value[0]), "y": float(value[1])}
    if not column_means[0] < column_means[1] < column_means[2]:
        raise ValueError("calibration columns overlap")
    if min(column_means[1] - column_means[0], column_means[2] - column_means[1]) < 2.0:
        raise ValueError("calibration columns are degenerate")
    return points


def detect_calibration_points(jpeg: bytes, *, y_down: bool = True) -> dict[str, dict[str, float]]:
    candidates = detect_candidates(jpeg)
    if len(candidates) < 9:
        raise ValueError(f"expected exactly 9 calibration circles, found {len(candidates)}")
    return assign_points(candidates[:9], y_down=y_down)


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
        [(float(points[f"{column}_{depth}"]["x"]), float(points[f"{column}_{depth}"]["y"])) for column in COLUMNS]
        for depth in DEPTHS
    ]

    def at(angle_deg: float, depth_mm: float) -> tuple[float, float]:
        horizontal = (float(angle_deg) + 30.0) / 60.0
        vertical = float(depth_mm) / 120.0
        column_points: list[tuple[float, float]] = []
        for column_index in range(3):
            p0, p1, p2 = (values[row][column_index] for row in range(3))
            column_points.append(
                (_quadratic(vertical, p0[0], p1[0], p2[0]), _quadratic(vertical, p0[1], p1[1], p2[1]))
            )
        return (
            _quadratic(horizontal, *(point[0] for point in column_points)),
            _quadratic(horizontal, *(point[1] for point in column_points)),
        )

    return at


def _angle_value(angle_min: float, angle_max: float) -> float:
    if angle_max <= 0:
        return angle_min
    if angle_min >= 0:
        return angle_max
    return 0.0


def build_calibration_grid(
    points: Mapping[str, Mapping[str, float]], *, angle_step: int = 5, depth_step: int = 5
) -> dict[str, Any]:
    if angle_step <= 0 or depth_step <= 0 or 60 % angle_step or 120 % depth_step:
        raise ValueError("grid steps must evenly divide 60 degrees and 120 millimeters")
    at = _interpolator(points)
    angles = list(range(-30, 31, angle_step))
    depths = list(range(0, 121, depth_step))
    zones: list[dict[str, Any]] = []
    for angle_index, (angle_min, angle_max) in enumerate(zip(angles, angles[1:])):
        for depth_index, (depth_min, depth_max) in enumerate(zip(depths, depths[1:])):
            polygon = [
                list(at(angle_min, depth_min)),
                list(at(angle_max, depth_min)),
                list(at(angle_max, depth_max)),
                list(at(angle_min, depth_max)),
            ]
            area = abs(float(cv2.contourArea(np.asarray(polygon, dtype=np.float32))))
            if area < 0.5:
                raise ValueError("calibration produced a degenerate grid zone")
            xs = [point[0] for point in polygon]
            ys = [point[1] for point in polygon]
            zones.append(
                {
                    "id": f"a{angle_index}-d{depth_index}",
                    "angle_index": angle_index,
                    "depth_index": depth_index,
                    "angle_min": float(angle_min),
                    "angle_max": float(angle_max),
                    "depth_min": float(depth_min),
                    "depth_max": float(depth_max),
                    "angle_value": _angle_value(float(angle_min), float(angle_max)),
                    "depth_value": float(depth_max),
                    "polygon": polygon,
                    "bbox": [min(xs), min(ys), max(xs), max(ys)],
                }
            )
    return {"angle_step": angle_step, "depth_step": depth_step, "angle_lines": angles, "depth_lines": depths, "zones": zones}


def _point_in_polygon(x: float, y: float, polygon: Sequence[Sequence[float]]) -> bool:
    contour = np.asarray(polygon, dtype=np.float32)
    return cv2.pointPolygonTest(contour, (float(x), float(y)), False) >= 0


def find_zone(x: float, y: float, grid: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for zone in grid.get("zones", []):
        min_x, min_y, max_x, max_y = zone["bbox"]
        if min_x <= x <= max_x and min_y <= y <= max_y and _point_in_polygon(x, y, zone["polygon"]):
            return zone
    return None


def _nearest_zone(x: float, y: float, grid: Mapping[str, Any]) -> Mapping[str, Any] | None:
    best: Mapping[str, Any] | None = None
    best_distance = float("inf")
    for zone in grid.get("zones", []):
        contour = np.asarray(zone["polygon"], dtype=np.float32)
        distance = abs(float(cv2.pointPolygonTest(contour, (float(x), float(y)), True)))
        if distance < best_distance:
            best = zone
            best_distance = distance
    return best


def reference_point(bbox_px: Sequence[float], reference_y_fraction: float) -> tuple[float, float]:
    if len(bbox_px) != 4:
        raise ValueError("bbox_px must contain four values")
    x0, y0, x1, y1 = (float(value) for value in bbox_px)
    x_low, x_high = sorted((x0, x1))
    y_low, y_high = sorted((y0, y1))
    fraction = min(max(float(reference_y_fraction), 0.0), 1.0)
    return (x_low + x_high) / 2.0, y_high - fraction * (y_high - y_low)


def project_detection(
    bbox_px: Sequence[float],
    grid: Mapping[str, Any],
    *,
    reference_y_fraction: float = 0.1,
    allow_extrapolation: bool = False,
) -> CalibrationProjection:
    x, y = reference_point(bbox_px, reference_y_fraction)
    zone = find_zone(x, y, grid)
    extrapolated = False
    if zone is None and allow_extrapolation:
        zone = _nearest_zone(x, y, grid)
        extrapolated = zone is not None
    if zone is None:
        return CalibrationProjection(
            angle_deg=0.0,
            distance_mm=0.0,
            z_height_mm=0.0,
            zone="",
            calibrated=False,
            extrapolated=False,
        )
    return CalibrationProjection(
        angle_deg=float(zone["angle_value"]),
        distance_mm=float(zone["depth_value"]),
        z_height_mm=0.0,
        zone=str(zone["id"]),
        calibrated=not extrapolated,
        extrapolated=extrapolated,
    )


def annotate_calibration(jpeg: bytes, points: Mapping[str, Mapping[str, float]], grid: Mapping[str, Any]) -> bytes:
    image = _decode_jpeg(jpeg)
    for zone in grid.get("zones", []):
        polygon = np.asarray(zone["polygon"], dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(image, [polygon], True, (80, 80, 80), 1, cv2.LINE_AA)
    for name, point in points.items():
        center = (int(round(float(point["x"]))), int(round(float(point["y"]))))
        cv2.circle(image, center, 7, (0, 0, 255), 2, cv2.LINE_AA)
        cv2.putText(image, name, (center[0] + 8, center[1] - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 200), 1)
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
        raise RuntimeError("failed to encode annotated calibration image")
    return encoded.tobytes()


def calibrate(jpeg: bytes, *, reference_y_fraction: float = 0.1) -> CalibrationResult:
    points = detect_calibration_points(jpeg)
    grid = build_calibration_grid(points)
    annotated = annotate_calibration(jpeg, points, grid)
    return CalibrationResult(points=points, grid=grid, annotated_jpeg=annotated)

