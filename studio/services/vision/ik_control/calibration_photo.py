"""Nine-point stencil extraction using Studio's existing Pillow dependency."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from io import BytesIO

from .calibration import COLUMNS, DEPTHS, build_grid


@dataclass(frozen=True)
class CalibrationResult:
    points: dict[str, dict[str, float]]
    grid: dict
    annotated_jpeg: bytes


def detect_calibration_points(jpeg: bytes) -> dict[str, dict[str, float]]:
    import numpy as np
    from PIL import Image, ImageFilter, ImageOps

    image = Image.open(BytesIO(jpeg)).convert("L")
    gray = np.asarray(ImageOps.autocontrast(image).filter(ImageFilter.GaussianBlur(2)), dtype=np.uint8)
    cutoff = min(105.0, float(np.percentile(gray, 18)))
    dark = gray <= cutoff
    height, width = dark.shape
    remaining = set(int(value) for value in np.flatnonzero(dark))
    candidates: list[tuple[float, float, int, float]] = []
    image_area = width * height
    minimum, maximum = image_area * 0.0001, image_area * 0.025
    while remaining:
        origin = remaining.pop()
        queue = deque((origin,))
        members = [origin]
        while queue:
            index = queue.popleft()
            y, x = divmod(index, width)
            for ny in range(max(0, y - 1), min(height, y + 2)):
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    neighbor = ny * width + nx
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        queue.append(neighbor)
                        members.append(neighbor)
        area = len(members)
        if not minimum <= area <= maximum:
            continue
        ys = [index // width for index in members]
        xs = [index % width for index in members]
        box_width, box_height = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        aspect = min(box_width, box_height) / max(box_width, box_height)
        fill = area / float(box_width * box_height)
        if aspect < 0.65 or not 0.42 <= fill <= 0.95:
            continue
        candidates.append((sum(xs) / area, sum(ys) / area, area, aspect + fill))
    candidates.sort(key=lambda value: (value[3], value[2]), reverse=True)
    return assign_points(candidates[:9])


def assign_points(candidates, *, y_down: bool = True) -> dict[str, dict[str, float]]:
    if len(candidates) != 9:
        raise ValueError(f"expected exactly 9 calibration circles, found {len(candidates)}")
    columns = [sorted(candidates, key=lambda item: item[0])[index : index + 3] for index in range(0, 9, 3)]
    points: dict[str, dict[str, float]] = {}
    means = []
    for column_name, values in zip(COLUMNS, columns):
        means.append(sum(item[0] for item in values) / 3.0)
        rows = sorted(values, key=lambda item: item[1], reverse=y_down)
        if min(abs(rows[index][1] - rows[index + 1][1]) for index in range(2)) < 2.0:
            raise ValueError(f"degenerate calibration column: {column_name}")
        for depth, value in zip(DEPTHS, rows):
            points[f"{column_name}_{depth}"] = {"x": float(value[0]), "y": float(value[1])}
    if not means[0] + 2 < means[1] or not means[1] + 2 < means[2]:
        raise ValueError("calibration columns overlap")
    return points


def calibrate(jpeg: bytes) -> CalibrationResult:
    from PIL import Image, ImageDraw

    points = detect_calibration_points(jpeg)
    grid = build_grid(points)
    image = Image.open(BytesIO(jpeg)).convert("RGB")
    draw = ImageDraw.Draw(image)
    for zone in grid["zones"]:
        draw.line([tuple(point) for point in (*zone["polygon"], zone["polygon"][0])], fill=(90, 90, 90), width=1)
    for name, point in points.items():
        x, y = point["x"], point["y"]
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), outline=(255, 0, 0), width=2)
        draw.text((x + 9, y - 8), name, fill=(200, 0, 0))
    output = BytesIO()
    image.save(output, format="JPEG", quality=92)
    return CalibrationResult(points, grid, output.getvalue())
