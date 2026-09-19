"""Nine-point camera calibration and deterministic image-to-IK projection.

The implementation intentionally depends only on Pillow and the standard
library.  It runs inside the isolated detector runtime, but keeping NumPy and
OpenCV out of this path also lets Studio's normal unit-test environment test
the exact production code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Mapping, Sequence

ANGLES = (-30, 0, 30)
LANES = ("left", "center", "right")
DEPTHS = (0, 60, 120)
THRESHOLDS = (8, 15, 22, 29)
ALGORITHM = "nine-dot-local-contrast.v2"


class CalibrationError(ValueError):
    """A safe calibration rejection with UI-friendly candidate details."""

    def __init__(self, code: str, message: str, *, candidates=()) -> None:
        super().__init__(message)
        self.code = code
        self.candidates = tuple(dict(item) for item in candidates)


@dataclass(frozen=True)
class CalibrationResult:
    width: int
    height: int
    points: dict[str, dict[str, float]]
    grid: dict[str, Any]
    candidates: tuple[dict[str, Any], ...]


def _components(mask: bytearray, width: int, height: int):
    """Yield connected-component measurements while consuming a binary mask."""
    for origin in range(width * height):
        if not mask[origin]:
            continue
        mask[origin] = 0
        stack = [origin]
        area = sum_x = sum_y = 0
        left = right = origin % width
        top = bottom = origin // width
        while stack:
            index = stack.pop()
            y, x = divmod(index, width)
            area += 1
            sum_x += x
            sum_y += y
            left, right = min(left, x), max(right, x)
            top, bottom = min(top, y), max(bottom, y)
            for ny in range(max(0, y - 1), min(height, y + 2)):
                row = ny * width
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    neighbor = row + nx
                    if mask[neighbor]:
                        mask[neighbor] = 0
                        stack.append(neighbor)
        yield area, sum_x / area, sum_y / area, left, top, right, bottom


def _candidate_components(jpeg: bytes) -> tuple[int, int, list[dict[str, Any]]]:
    from PIL import Image, ImageChops, ImageFilter, ImageOps

    try:
        image = Image.open(BytesIO(jpeg)).convert("L")
        image.load()
    except Exception as exc:
        raise CalibrationError("invalid_image", f"The calibration JPEG could not be decoded: {exc}") from exc

    width, height = image.size
    if width < 160 or height < 120:
        raise CalibrationError("image_too_small", "Calibration needs an image of at least 160×120 pixels.")

    normalized = ImageOps.autocontrast(image)
    foreground = normalized.filter(ImageFilter.GaussianBlur(1.0))
    background = normalized.filter(ImageFilter.GaussianBlur(max(8.0, min(width, height) / 32.0)))
    contrast = ImageChops.subtract(background, foreground)
    contrast_bytes = contrast.tobytes()
    image_area = width * height
    # Real dots in the 800×600 camera frames occupy 170–550 pixels.  Tiny
    # edge highlights can still be beautifully round, so shape alone is not
    # enough; this scale-aware floor rejects those 50–60 pixel reflections
    # while retaining equivalently sized dots at the firmware's VGA/QVGA
    # fallback resolutions.
    minimum_area = max(8, int(image_area * 0.00018))
    maximum_area = max(minimum_area + 1, int(image_area * 0.0040))
    found: list[dict[str, Any]] = []

    for threshold in THRESHOLDS:
        mask = bytearray(255 if value >= threshold else 0 for value in contrast_bytes)
        for area, x, y, left, top, right, bottom in _components(mask, width, height):
            if not minimum_area <= area <= maximum_area:
                continue
            box_width, box_height = right - left + 1, bottom - top + 1
            aspect = min(box_width, box_height) / max(box_width, box_height)
            fill = area / float(box_width * box_height)
            if aspect < 0.62 or fill < 0.68:
                continue
            local_contrast = sum(
                contrast_bytes[row * width + column]
                for row in range(top, bottom + 1)
                for column in range(left, right + 1)
            ) / float(box_width * box_height)
            found.append({
                "x": float(x), "y": float(y), "area": area,
                "width": box_width, "height": box_height,
                "aspect": round(aspect, 4), "fill": round(fill, 4),
                "contrast": round(local_contrast, 3), "threshold": threshold,
            })

    # The same dot is found at several contrast thresholds.  Merge those
    # observations while retaining the cleanest outline and the evidence that
    # it remained stable across thresholds.
    merge_radius = max(2.5, min(width, height) * 0.0125)
    groups: list[list[dict[str, Any]]] = []
    for candidate in sorted(found, key=lambda item: (item["y"], item["x"], item["threshold"])):
        group = next((items for items in groups if math.hypot(
            candidate["x"] - sum(item["x"] for item in items) / len(items),
            candidate["y"] - sum(item["y"] for item in items) / len(items),
        ) <= merge_radius), None)
        if group is None:
            groups.append([candidate])
        else:
            group.append(candidate)

    candidates = []
    for group in groups:
        best = max(group, key=lambda item: (
            item["aspect"] + item["fill"], item["contrast"], item["area"],
        ))
        candidate = dict(best)
        candidate["threshold_count"] = len({item["threshold"] for item in group})
        candidate["score"] = round(
            best["aspect"] + best["fill"] + min(candidate["threshold_count"], 4) * 0.15,
            4,
        )
        candidates.append(candidate)
    candidates.sort(key=lambda item: (item["y"], item["x"]))
    return width, height, candidates


def assign_points(
    candidates: Sequence[Mapping[str, Any]], width: int, height: int,
) -> dict[str, dict[str, float]]:
    diagnostics = [dict(item) for item in candidates]
    if len(candidates) != 9:
        code = "ambiguous_dots" if len(candidates) > 9 else "missing_dots"
        raise CalibrationError(
            code,
            f"Expected one unambiguous 3×3 target; found {len(candidates)} usable circles.",
            candidates=diagnostics,
        )

    ordered = sorted(candidates, key=lambda item: (float(item["y"]), float(item["x"])))
    rows = [ordered[index:index + 3] for index in range(0, 9, 3)]
    minimum_row_gap = max(3.0, height * 0.03)
    if any(
        min(float(item["y"]) for item in rows[index + 1])
        - max(float(item["y"]) for item in rows[index]) < minimum_row_gap
        for index in range(2)
    ):
        raise CalibrationError(
            "ambiguous_rows", "The three target depths are not cleanly separated.",
            candidates=diagnostics,
        )

    minimum_lane_gap = max(3.0, width * 0.04)
    labelled: dict[str, dict[str, float]] = {}
    # Image y grows downward, so the top row is the far 120 mm row.
    for depth, row in zip((120, 60, 0), rows):
        by_x = sorted(row, key=lambda item: float(item["x"]))
        if any(
            float(by_x[index + 1]["x"]) - float(by_x[index]["x"]) < minimum_lane_gap
            for index in range(2)
        ):
            raise CalibrationError(
                "ambiguous_lanes", f"The {depth} mm row does not contain three separated lanes.",
                candidates=diagnostics,
            )
        for lane, item in zip(LANES, by_x):
            labelled[f"{lane}_{depth}"] = {"x": float(item["x"]), "y": float(item["y"])}

    for lane in LANES:
        near, middle, far = (labelled[f"{lane}_{depth}"] for depth in DEPTHS)
        if not far["y"] < middle["y"] < near["y"]:
            raise CalibrationError(
                "crossed_lanes", f"The {lane} lane crosses or reverses depth order.",
                candidates=diagnostics,
            )
    return labelled


def _quadratic(t: float, p0: float, p1: float, p2: float) -> float:
    return (
        2.0 * (t - 0.5) * (t - 1.0) * p0
        - 4.0 * t * (t - 1.0) * p1
        + 2.0 * t * (t - 0.5) * p2
    )


def _interpolator(points: Mapping[str, Mapping[str, float]]):
    required = [f"{lane}_{depth}" for lane in LANES for depth in DEPTHS]
    missing = [key for key in required if key not in points]
    if missing:
        raise ValueError(f"missing calibration points: {', '.join(missing)}")
    values = [[
        (float(points[f"{lane}_{depth}"]["x"]), float(points[f"{lane}_{depth}"]["y"]))
        for lane in LANES
    ] for depth in DEPTHS]

    def at(angle_deg: float, depth_mm: float) -> tuple[float, float]:
        horizontal = (float(angle_deg) + 30.0) / 60.0
        vertical = float(depth_mm) / 120.0
        columns = []
        for index in range(3):
            p0, p1, p2 = (values[row][index] for row in range(3))
            columns.append((
                _quadratic(vertical, p0[0], p1[0], p2[0]),
                _quadratic(vertical, p0[1], p1[1], p2[1]),
            ))
        return (
            _quadratic(horizontal, columns[0][0], columns[1][0], columns[2][0]),
            _quadratic(horizontal, columns[0][1], columns[1][1], columns[2][1]),
        )

    return at


def _polygon_area(points: Sequence[Sequence[float]]) -> float:
    return abs(sum(
        float(points[index][0]) * float(points[(index + 1) % len(points)][1])
        - float(points[(index + 1) % len(points)][0]) * float(points[index][1])
        for index in range(len(points))
    ) / 2.0)


def build_grid(points: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    at = _interpolator(points)
    angles = list(range(-30, 31, 5))
    distances = list(range(0, 121, 5))
    zones = []
    for angle_index, (angle_min, angle_max) in enumerate(zip(angles, angles[1:])):
        for distance_index, (distance_min, distance_max) in enumerate(zip(distances, distances[1:])):
            polygon = [
                list(at(angle_min, distance_min)), list(at(angle_max, distance_min)),
                list(at(angle_max, distance_max)), list(at(angle_min, distance_max)),
            ]
            if not all(math.isfinite(value) for point in polygon for value in point):
                raise CalibrationError("invalid_grid", "Calibration produced non-finite grid coordinates.")
            if _polygon_area(polygon) < 0.5:
                raise CalibrationError("degenerate_grid", "Calibration produced a degenerate grid cell.")
            xs, ys = [point[0] for point in polygon], [point[1] for point in polygon]
            center_px = list(at((angle_min + angle_max) / 2.0, (distance_min + distance_max) / 2.0))
            zones.append({
                "id": f"a{angle_index}-d{distance_index}",
                "angle_min": float(angle_min), "angle_max": float(angle_max),
                "distance_min": float(distance_min), "distance_max": float(distance_max),
                "angle_deg": (angle_min + angle_max) / 2.0,
                "distance_mm": (distance_min + distance_max) / 2.0,
                "polygon": polygon, "bbox": [min(xs), min(ys), max(xs), max(ys)],
                "center_px": center_px,
            })
    return {
        "schema": "nine-point-grid.v2", "angle_step_deg": 5,
        "distance_step_mm": 5, "angle_lines": angles,
        "distance_lines": distances, "zones": zones,
    }


def calibrate(jpeg: bytes) -> CalibrationResult:
    width, height, candidates = _candidate_components(jpeg)
    points = assign_points(candidates, width, height)
    return CalibrationResult(width, height, points, build_grid(points), tuple(candidates))


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
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
        previous = current
    return inside


def find_zone(x: float, y: float, grid: Mapping[str, Any]):
    matches = []
    for zone in grid.get("zones", ()):
        left, top, right, bottom = zone["bbox"]
        if left <= x <= right and top <= y <= bottom and _contains(x, y, zone["polygon"]):
            center_x, center_y = zone["center_px"]
            matches.append((math.hypot(x - center_x, y - center_y), str(zone["id"]), zone))
    return min(matches, key=lambda item: (item[0], item[1]))[2] if matches else None


def reference_point(box_px: Sequence[float], fraction: float = 0.1) -> tuple[float, float]:
    if len(box_px) != 4:
        raise ValueError("box_px must contain four values")
    x0, y0, x1, y1 = (float(value) for value in box_px)
    if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
        raise ValueError("box_px must contain finite values")
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    fraction = min(max(float(fraction), 0.0), 1.0)
    return (left + right) / 2.0, bottom - fraction * (bottom - top)


def project(box_px: Sequence[float], profile: Mapping[str, Any]) -> dict[str, Any]:
    x, y = reference_point(box_px)
    zone = find_zone(x, y, profile.get("grid", {}))
    accepted = zone is not None
    return {
        "schema": "desk_buddy.vision.ik-target.v1",
        "profile_id": str(profile.get("profile_id") or ""),
        "reference_px": [x, y],
        "cell_id": str(zone["id"]) if zone else "",
        "angle_deg": float(zone["angle_deg"]) if zone else 0.0,
        "distance_mm": float(zone["distance_mm"]) if zone else 0.0,
        "z_height_mm": 0.0,
        "accepted": accepted,
        "rejection_reasons": [] if accepted else ["detection_outside_calibrated_workspace"],
    }
