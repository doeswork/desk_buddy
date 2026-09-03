"""Dependency-light, versioned contracts shared by Studio and vision workers."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

CONTRACT_SCHEMA = "desk_buddy.vision.worker.v1"
DETECTION_SCHEMA = "detections.v1"
DEPTH_SCHEMA = "depth.v1"
OBSERVATION_SCHEMA = "vision-observation.v1"
IK_PREDICTION_SCHEMA = "ik-prediction.v1"
IK_TARGET_SCHEMA = "ik-target.v1"
FEATURE_SCHEMA = "vision-features.v1"

JOB_KINDS = frozenset(
    {
        "zero_shot.infer",
        "depth.infer",
        "ik_model.train",
        "ik_model.infer",
        "ik_model.load",
        "job.status",
        "artifact.resend",
    }
)
JOB_STATUSES = frozenset({"accepted", "waiting_for_artifacts", "processing", "completed", "failed"})
FORBIDDEN_KEYS = frozenset(
    {
        "path",
        "file_path",
        "filesystem_path",
        "model_path",
        "shell",
        "shell_command",
        "executable",
        "password",
        "credential",
        "credentials",
        "token",
        "api_key",
        "secret",
        "command",
        "code",
        "script",
    }
)


class ContractError(ValueError):
    def __init__(self, code: str, message: str, *, field_name: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field_name = field_name

    def as_dict(self) -> dict[str, str]:
        result = {"code": self.code, "message": self.message}
        if self.field_name:
            result["field"] = self.field_name
        return result


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ContractError("missing_field", f"{name} must be a non-empty string", field_name=name)
    return result


def _finite(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractError("invalid_field", f"{name} must be numeric", field_name=name) from exc
    if not math.isfinite(result):
        raise ContractError("invalid_field", f"{name} must be finite", field_name=name)
    return result


def _tuple(values: Sequence[Any], length: int, name: str) -> tuple[float, ...]:
    if len(values) != length:
        raise ContractError("invalid_field", f"{name} must contain {length} values", field_name=name)
    return tuple(_finite(value, name) for value in values)


def _assert_safe_payload(value: Any, prefix: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key)
            if name.lower() in FORBIDDEN_KEYS:
                raise ContractError("forbidden_field", f"{prefix}.{name} is not allowed", field_name=f"{prefix}.{name}")
            _assert_safe_payload(child, f"{prefix}.{name}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_safe_payload(child, f"{prefix}[{index}]")


@dataclass(frozen=True)
class DetectionV1:
    label: str
    score: float
    bbox_px: tuple[float, float, float, float]
    bbox_norm: tuple[float, float, float, float]
    center_px: tuple[float, float]
    center_norm: tuple[float, float]
    area_norm: float
    model_id: str
    model_version: str

    @classmethod
    def create(
        cls,
        *,
        label: str,
        score: Any,
        bbox_px: Sequence[Any],
        image_width: int,
        image_height: int,
        model_id: str,
        model_version: str,
    ) -> "DetectionV1":
        if image_width < 1 or image_height < 1:
            raise ContractError("invalid_field", "image dimensions must be positive")
        x0, y0, x1, y1 = _tuple(bbox_px, 4, "bbox_px")
        if x1 < x0 or y1 < y0:
            raise ContractError("invalid_field", "bbox_px coordinates are reversed", field_name="bbox_px")
        x0, x1 = (min(max(value, 0.0), float(image_width)) for value in (x0, x1))
        y0, y1 = (min(max(value, 0.0), float(image_height)) for value in (y0, y1))
        bbox_norm = (x0 / image_width, y0 / image_height, x1 / image_width, y1 / image_height)
        center_px = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
        center_norm = (center_px[0] / image_width, center_px[1] / image_height)
        area = max(0.0, bbox_norm[2] - bbox_norm[0]) * max(0.0, bbox_norm[3] - bbox_norm[1])
        confidence = _finite(score, "score")
        if not 0.0 <= confidence <= 1.0:
            raise ContractError("invalid_field", "score must be within [0,1]", field_name="score")
        return cls(
            label=_text(label, "label"),
            score=confidence,
            bbox_px=(x0, y0, x1, y1),
            bbox_norm=bbox_norm,
            center_px=center_px,
            center_norm=center_norm,
            area_norm=area,
            model_id=_text(model_id, "model_id"),
            model_version=_text(model_version, "model_version"),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DetectionV1":
        return cls(
            label=_text(raw.get("label"), "label"),
            score=_finite(raw.get("score"), "score"),
            bbox_px=_tuple(raw.get("bbox_px", ()), 4, "bbox_px"),
            bbox_norm=_tuple(raw.get("bbox_norm", ()), 4, "bbox_norm"),
            center_px=_tuple(raw.get("center_px", ()), 2, "center_px"),
            center_norm=_tuple(raw.get("center_norm", ()), 2, "center_norm"),
            area_norm=_finite(raw.get("area_norm"), "area_norm"),
            model_id=_text(raw.get("model_id"), "model_id"),
            model_version=_text(raw.get("model_version"), "model_version"),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DetectionBatchV1:
    image_width: int
    image_height: int
    prompt: str
    detections: tuple[DetectionV1, ...]
    model_id: str
    model_version: str
    schema: str = DETECTION_SCHEMA

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DetectionBatchV1":
        if raw.get("schema") != DETECTION_SCHEMA:
            raise ContractError("unsupported_schema", f"detection schema must be {DETECTION_SCHEMA}")
        detections = raw.get("detections", [])
        if not isinstance(detections, list):
            raise ContractError("invalid_field", "detections must be a list")
        return cls(
            image_width=int(raw["image_width"]),
            image_height=int(raw["image_height"]),
            prompt=_text(raw.get("prompt"), "prompt"),
            detections=tuple(DetectionV1.from_dict(item) for item in detections),
            model_id=_text(raw.get("model_id"), "model_id"),
            model_version=_text(raw.get("model_version"), "model_version"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "prompt": self.prompt,
            "detections": [item.as_dict() for item in self.detections],
            "model_id": self.model_id,
            "model_version": self.model_version,
        }


@dataclass(frozen=True)
class DepthMapV1:
    width: int
    height: int
    normalized_artifact_id: str
    preview_artifact_id: str
    native_units: str
    native_near_is_high: bool
    model_id: str
    model_version: str
    dtype: str = "float32"
    canonical_near_is_high: bool = True
    schema: str = DEPTH_SCHEMA

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DepthMapV1":
        if raw.get("schema") != DEPTH_SCHEMA:
            raise ContractError("unsupported_schema", f"depth schema must be {DEPTH_SCHEMA}")
        width, height = int(raw["width"]), int(raw["height"])
        if width < 1 or height < 1:
            raise ContractError("invalid_field", "depth dimensions must be positive")
        canonical_near_is_high = bool(raw.get("canonical_near_is_high", True))
        if not canonical_near_is_high:
            raise ContractError("invalid_field", "DepthMapV1 must encode nearer values as 1")
        dtype = str(raw.get("dtype") or "float32")
        if dtype != "float32":
            raise ContractError("invalid_field", "DepthMapV1 dtype must be float32")
        return cls(
            width=width,
            height=height,
            normalized_artifact_id=_text(raw.get("normalized_artifact_id"), "normalized_artifact_id"),
            preview_artifact_id=_text(raw.get("preview_artifact_id"), "preview_artifact_id"),
            native_units=str(raw.get("native_units") or "relative"),
            native_near_is_high=bool(raw.get("native_near_is_high")),
            model_id=_text(raw.get("model_id"), "model_id"),
            model_version=_text(raw.get("model_version"), "model_version"),
            dtype=dtype,
            canonical_near_is_high=canonical_near_is_high,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VisionObservationV1:
    capture_id: str
    robot_id: str
    prompt: str
    detection: DetectionV1
    depth_patch_64x64: tuple[float, ...] = field(repr=False)
    depth_statistics: dict[str, float] = field(default_factory=dict)
    detector_model_id: str = ""
    detector_model_version: str = ""
    depth_model_id: str = ""
    depth_model_version: str = ""
    calibration_profile_id: str = ""
    schema: str = OBSERVATION_SCHEMA

    def __post_init__(self) -> None:
        if len(self.depth_patch_64x64) != 4096:
            raise ContractError("invalid_field", "depth_patch_64x64 must contain 4096 values")
        if any(not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0 for value in self.depth_patch_64x64):
            raise ContractError("invalid_field", "depth patch values must be finite and within [0,1]")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "VisionObservationV1":
        if raw.get("schema") != OBSERVATION_SCHEMA:
            raise ContractError("unsupported_schema", f"observation schema must be {OBSERVATION_SCHEMA}")
        patch = raw.get("depth_patch_64x64", [])
        if not isinstance(patch, list):
            raise ContractError("invalid_field", "depth_patch_64x64 must be a list")
        return cls(
            capture_id=_text(raw.get("capture_id"), "capture_id"),
            robot_id=_text(raw.get("robot_id"), "robot_id"),
            prompt=_text(raw.get("prompt"), "prompt"),
            detection=DetectionV1.from_dict(raw.get("detection", {})),
            depth_patch_64x64=tuple(float(value) for value in patch),
            depth_statistics={str(key): _finite(value, str(key)) for key, value in dict(raw.get("depth_statistics", {})).items()},
            detector_model_id=_text(raw.get("detector_model_id"), "detector_model_id"),
            detector_model_version=_text(raw.get("detector_model_version"), "detector_model_version"),
            depth_model_id=_text(raw.get("depth_model_id"), "depth_model_id"),
            depth_model_version=_text(raw.get("depth_model_version"), "depth_model_version"),
            calibration_profile_id=str(raw.get("calibration_profile_id") or ""),
        )

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["detection"] = self.detection.as_dict()
        result["depth_patch_64x64"] = list(self.depth_patch_64x64)
        return result


@dataclass(frozen=True)
class IKPredictionV1:
    rotation_deg: float
    distance_mm: float
    z_height_mm: float
    model_id: str
    model_version: str
    confidence: dict[str, float] = field(default_factory=dict)
    schema: str = IK_PREDICTION_SCHEMA

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "IKPredictionV1":
        if raw.get("schema") != IK_PREDICTION_SCHEMA:
            raise ContractError("unsupported_schema", f"prediction schema must be {IK_PREDICTION_SCHEMA}")
        return cls(
            rotation_deg=_finite(raw.get("rotation_deg"), "rotation_deg"),
            distance_mm=_finite(raw.get("distance_mm"), "distance_mm"),
            z_height_mm=_finite(raw.get("z_height_mm"), "z_height_mm"),
            model_id=_text(raw.get("model_id"), "model_id"),
            model_version=_text(raw.get("model_version"), "model_version"),
            confidence={str(key): _finite(value, str(key)) for key, value in dict(raw.get("confidence", {})).items()},
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IKTargetV1:
    rotation_deg: float
    distance_mm: float
    z_height_mm: float
    strategy_id: str
    model_id: str | None = None
    accepted: bool = True
    rejection_reasons: tuple[str, ...] = ()
    schema: str = IK_TARGET_SCHEMA

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class IKStrategy(Protocol):
    strategy_id: str
    required_inputs: frozenset[str]

    def plan(self, observation: Any) -> IKTargetV1: ...


@dataclass(frozen=True)
class JobEnvelope:
    job_id: str
    request_id: str
    capture_id: str
    robot_id: str
    worker_id: str
    kind: str
    model_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    input_artifact_roles: tuple[str, ...] = ()
    created_at: str = field(default_factory=utc_now)
    schema: str = CONTRACT_SCHEMA

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "JobEnvelope":
        if raw.get("schema") != CONTRACT_SCHEMA:
            raise ContractError("unsupported_schema", f"job schema must be {CONTRACT_SCHEMA}")
        kind = _text(raw.get("kind"), "kind")
        if kind not in JOB_KINDS:
            raise ContractError("unknown_job_kind", f"unsupported job kind: {kind}")
        payload = raw.get("payload", {})
        if not isinstance(payload, dict):
            raise ContractError("invalid_field", "payload must be an object", field_name="payload")
        _assert_safe_payload(payload)
        roles = raw.get("input_artifact_roles", [])
        if not isinstance(roles, list) or any(not isinstance(role, str) or not role for role in roles):
            raise ContractError("invalid_field", "input_artifact_roles must be a list of strings")
        return cls(
            job_id=_text(raw.get("job_id"), "job_id"),
            request_id=_text(raw.get("request_id"), "request_id"),
            capture_id=_text(raw.get("capture_id"), "capture_id"),
            robot_id=_text(raw.get("robot_id"), "robot_id"),
            worker_id=_text(raw.get("worker_id"), "worker_id"),
            kind=kind,
            model_id=_text(raw.get("model_id"), "model_id"),
            payload=dict(payload),
            input_artifact_roles=tuple(roles),
            created_at=_text(raw.get("created_at"), "created_at"),
        )

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["input_artifact_roles"] = list(self.input_artifact_roles)
        return result


@dataclass(frozen=True)
class JobEvent:
    job_id: str
    request_id: str
    capture_id: str
    robot_id: str
    worker_id: str
    kind: str
    model_id: str
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    model_version: str = ""
    created_at: str = field(default_factory=utc_now)
    schema: str = CONTRACT_SCHEMA

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "JobEvent":
        if raw.get("schema") != CONTRACT_SCHEMA:
            raise ContractError("unsupported_schema", f"event schema must be {CONTRACT_SCHEMA}")
        status = _text(raw.get("status"), "status")
        if status not in JOB_STATUSES:
            raise ContractError("invalid_status", f"unsupported job status: {status}")
        error = raw.get("error")
        if error is not None and not isinstance(error, dict):
            raise ContractError("invalid_field", "error must be an object or null")
        return cls(
            job_id=_text(raw.get("job_id"), "job_id"),
            request_id=_text(raw.get("request_id"), "request_id"),
            capture_id=_text(raw.get("capture_id"), "capture_id"),
            robot_id=_text(raw.get("robot_id"), "robot_id"),
            worker_id=_text(raw.get("worker_id"), "worker_id"),
            kind=_text(raw.get("kind"), "kind"),
            model_id=_text(raw.get("model_id"), "model_id"),
            status=status,
            payload=dict(raw.get("payload", {})),
            error=dict(error) if error is not None else None,
            model_version=str(raw.get("model_version") or ""),
            created_at=str(raw.get("created_at") or utc_now()),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkerStatus:
    worker_id: str
    worker_kind: str
    host: str
    device: str
    ready: bool
    busy: bool
    job_kinds: tuple[str, ...]
    models: tuple[dict[str, Any], ...]
    input_schemas: tuple[str, ...]
    output_schemas: tuple[str, ...]
    updated_at: str = field(default_factory=utc_now)
    schema: str = CONTRACT_SCHEMA

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "WorkerStatus":
        if raw.get("schema") != CONTRACT_SCHEMA:
            raise ContractError("unsupported_schema", f"status schema must be {CONTRACT_SCHEMA}")
        return cls(
            worker_id=_text(raw.get("worker_id"), "worker_id"),
            worker_kind=_text(raw.get("worker_kind"), "worker_kind"),
            host=str(raw.get("host") or ""),
            device=str(raw.get("device") or "unknown"),
            ready=bool(raw.get("ready")),
            busy=bool(raw.get("busy")),
            job_kinds=tuple(str(item) for item in raw.get("job_kinds", [])),
            models=tuple(dict(item) for item in raw.get("models", [])),
            input_schemas=tuple(str(item) for item in raw.get("input_schemas", [])),
            output_schemas=tuple(str(item) for item in raw.get("output_schemas", [])),
            updated_at=str(raw.get("updated_at") or utc_now()),
        )

    @classmethod
    def offline(cls, worker_id: str, worker_kind: str) -> "WorkerStatus":
        return cls(worker_id, worker_kind, "", "unknown", False, False, (), (), (), ())

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for key in ("job_kinds", "models", "input_schemas", "output_schemas"):
            result[key] = list(result[key])
        return result

    def supports(self, model_id: str, kind: str) -> bool:
        if not self.ready or kind not in self.job_kinds:
            return False
        # A configured inference worker can receive a new model bundle before
        # that model appears in its retained status advertisement.
        if kind == "ik_model.load":
            return True
        return any(model.get("model_id") == model_id for model in self.models)
