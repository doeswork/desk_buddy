from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

FEATURE_SCHEMA_V1 = "features.v1"


def _finite_float(value: Any, name: str) -> float:
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be finite")
    return number


def _box(values: Sequence[Any], name: str) -> tuple[float, float, float, float]:
    if len(values) != 4:
        raise ValueError(f"{name} must contain four values")
    x0, y0, x1, y1 = (_finite_float(value, name) for value in values)
    if x1 < x0 or y1 < y0:
        raise ValueError(f"{name} has reversed coordinates")
    return x0, y0, x1, y1


@dataclass(frozen=True)
class Detection:
    label: str
    score: float
    bbox_px: tuple[float, float, float, float]
    bbox_norm: tuple[float, float, float, float]
    center_px: tuple[float, float]
    center_norm: tuple[float, float]
    area_norm: float
    source_model_id: str
    source_model_version: str

    @classmethod
    def create(
        cls,
        *,
        label: str,
        score: Any,
        bbox_px: Sequence[Any],
        image_width: int,
        image_height: int,
        source_model_id: str,
        source_model_version: str,
    ) -> "Detection":
        if image_width < 1 or image_height < 1:
            raise ValueError("image dimensions must be positive")
        x0, y0, x1, y1 = _box(bbox_px, "bbox_px")
        x0 = min(max(x0, 0.0), float(image_width))
        x1 = min(max(x1, 0.0), float(image_width))
        y0 = min(max(y0, 0.0), float(image_height))
        y1 = min(max(y1, 0.0), float(image_height))
        normalized = (x0 / image_width, y0 / image_height, x1 / image_width, y1 / image_height)
        center_px = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
        center_norm = (center_px[0] / image_width, center_px[1] / image_height)
        area_norm = max(0.0, normalized[2] - normalized[0]) * max(0.0, normalized[3] - normalized[1])
        return cls(
            label=str(label),
            score=_finite_float(score, "score"),
            bbox_px=(x0, y0, x1, y1),
            bbox_norm=normalized,
            center_px=center_px,
            center_norm=center_norm,
            area_norm=area_norm,
            source_model_id=str(source_model_id),
            source_model_version=str(source_model_version),
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "Detection":
        return cls(
            label=str(raw["label"]),
            score=_finite_float(raw["score"], "score"),
            bbox_px=_box(raw["bbox_px"], "bbox_px"),
            bbox_norm=_box(raw["bbox_norm"], "bbox_norm"),
            center_px=tuple(float(v) for v in raw["center_px"]),
            center_norm=tuple(float(v) for v in raw["center_norm"]),
            area_norm=_finite_float(raw["area_norm"], "area_norm"),
            source_model_id=str(raw["source_model_id"]),
            source_model_version=str(raw["source_model_version"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DetectionBatch:
    image_width: int
    image_height: int
    phrase: str
    detections: tuple[Detection, ...]
    model_id: str
    model_version: str
    output_schema: str = "detections.v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "output_schema": self.output_schema,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "phrase": self.phrase,
            "detections": [detection.as_dict() for detection in self.detections],
            "model_id": self.model_id,
            "model_version": self.model_version,
        }


@dataclass(frozen=True)
class DepthMap:
    raw_artifact_id: str
    normalized_artifact_id: str | None
    preview_artifact_id: str | None
    width: int
    height: int
    dtype: str
    min_value: float
    max_value: float
    units: str
    source_model_id: str
    source_model_version: str
    scale: float | None = None
    offset: float | None = None
    output_schema: str = "depth.v1"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlanCorrections:
    rotation_delta_deg: float = 0.0
    distance_delta_mm: float = 0.0
    z_height_delta_mm: float = 0.0
    twist_position: int | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PlanCorrections":
        return cls(
            rotation_delta_deg=_finite_float(raw.get("rotation_delta_deg", 0.0), "rotation_delta_deg"),
            distance_delta_mm=_finite_float(raw.get("distance_delta_mm", 0.0), "distance_delta_mm"),
            z_height_delta_mm=_finite_float(raw.get("z_height_delta_mm", 0.0), "z_height_delta_mm"),
            twist_position=int(raw["twist_position"]) if raw.get("twist_position") is not None else None,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureSet:
    image_width: int
    image_height: int
    phrase: str
    selected_label: str
    detection_score: float
    bbox_norm: tuple[float, float, float, float]
    bbox_center_norm: tuple[float, float]
    bbox_area_norm: float
    depth_patch_64x64: tuple[float, ...] = field(repr=False)
    depth_mean: float = 0.0
    depth_median: float = 0.0
    depth_std: float = 0.0
    depth_min: float = 0.0
    depth_max: float = 0.0
    depth_percentiles: tuple[float, ...] = ()
    calibration_angle_deg: float = 0.0
    calibration_distance_mm: float = 0.0
    calibration_z_height_mm: float = 0.0
    calibration_zone: str = ""
    calibration_extrapolated: bool = False
    baseline_rotation_deg: float = 0.0
    baseline_controlik_distance_mm: float = 0.0
    baseline_controlik_z_height_mm: float = 0.0
    schema: str = FEATURE_SCHEMA_V1

    def __post_init__(self) -> None:
        if len(self.depth_patch_64x64) != 4096:
            raise ValueError("depth_patch_64x64 must contain exactly 4096 values")

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["depth_patch_64x64"] = list(self.depth_patch_64x64)
        return result

