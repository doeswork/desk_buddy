"""Validation and normalization for zero-shot detection results."""

from __future__ import annotations

import math
from typing import Iterable, Mapping

DETECTION_SCHEMA = "detections.v1"


def normalize_batch(
    *,
    width: int,
    height: int,
    prompt: str,
    model_id: str,
    revision: str,
    detections: Iterable[Mapping],
) -> dict:
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")
    clean = []
    for item in detections:
        try:
            label = str(item.get("label") or prompt).strip()
            score = float(item["score"])
            values = [float(value) for value in item["box"]]
        except (KeyError, TypeError, ValueError):
            continue
        if not label or len(values) != 4 or not math.isfinite(score) or not 0 <= score <= 1:
            continue
        if any(not math.isfinite(value) for value in values):
            continue
        x0, y0, x1, y1 = values
        x0, x1 = max(0.0, min(x0, width)), max(0.0, min(x1, width))
        y0, y1 = max(0.0, min(y0, height)), max(0.0, min(y1, height))
        if x1 <= x0 or y1 <= y0:
            continue
        center_x, center_y = (x0 + x1) / 2, (y0 + y1) / 2
        area = (x1 - x0) * (y1 - y0)
        clean.append({
            "label": label,
            "score": score,
            "box_px": [x0, y0, x1, y1],
            "box_normalized": [x0 / width, y0 / height, x1 / width, y1 / height],
            "center_px": [center_x, center_y],
            "center_normalized": [center_x / width, center_y / height],
            "area_normalized": area / (width * height),
            "model_id": model_id,
            "revision": revision,
        })
    clean.sort(key=lambda item: (
        -item["score"], *item["box_px"], item["label"],
    ))
    return {
        "schema": DETECTION_SCHEMA,
        "image_width": width,
        "image_height": height,
        "prompt": prompt,
        "detections": clean,
        "model_id": model_id,
        "revision": revision,
    }
