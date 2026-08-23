from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from desk_buddy_vision_protocol import PlanCorrections

from .sequencer import RobotCommand


@dataclass(frozen=True)
class CalibrationProjection:
    angle_deg: float
    distance_mm: float
    z_height_mm: float
    zone: str
    calibrated: bool
    extrapolated: bool

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "CalibrationProjection":
        return cls(
            angle_deg=float(raw["angle_deg"]),
            distance_mm=float(raw["distance_mm"]),
            z_height_mm=float(raw.get("z_height_mm", 0.0)),
            zone=str(raw.get("zone") or ""),
            calibrated=bool(raw.get("calibrated")),
            extrapolated=bool(raw.get("extrapolated")),
        )


@dataclass(frozen=True)
class MotionTarget:
    rotation_deg: float
    distance_mm: float
    z_height_mm: float


@dataclass(frozen=True)
class SafetyLimits:
    max_rotation_deg: float = 45.0
    min_distance_mm: float = 0.0
    max_distance_mm: float = 180.0
    min_z_height_mm: float = -30.0
    max_z_height_mm: float = 120.0
    max_rotation_correction_deg: float = 10.0
    max_distance_correction_mm: float = 20.0
    max_z_correction_mm: float = 20.0
    allow_extrapolated_motion: bool = False


@dataclass(frozen=True)
class SafetyResult:
    status: str
    reasons: tuple[str, ...]
    corrections_clamped: bool = False

    @property
    def safe(self) -> bool:
        return self.status == "safe"


@dataclass(frozen=True)
class MotionPlan:
    plan_id: str
    operation_id: str
    planner_mode: str
    planner_model_id: str | None
    baseline: MotionTarget
    corrections: PlanCorrections
    final_target: MotionTarget
    commands: tuple[RobotCommand, ...]
    safety: SafetyResult

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "operation_id": self.operation_id,
            "planner_mode": self.planner_mode,
            "planner_model_id": self.planner_model_id,
            "baseline": asdict(self.baseline),
            "corrections": self.corrections.as_dict(),
            "final_target": asdict(self.final_target),
            "commands": [
                {"action": command.action, "body": dict(command.body), "role": command.role}
                for command in self.commands
            ],
            "safety": {
                "status": self.safety.status,
                "reasons": list(self.safety.reasons),
                "corrections_clamped": self.safety.corrections_clamped,
            },
        }


def _clamp(value: float, lower: float, upper: float) -> tuple[float, bool]:
    result = min(max(float(value), lower), upper)
    return result, result != float(value)


def _validate_target(target: MotionTarget, projection: CalibrationProjection, limits: SafetyLimits) -> list[str]:
    reasons: list[str] = []
    if not projection.calibrated and not projection.extrapolated:
        reasons.append("calibration_projection_unavailable")
    if projection.extrapolated and not limits.allow_extrapolated_motion:
        reasons.append("extrapolated_motion_disabled")
    if abs(target.rotation_deg) > limits.max_rotation_deg:
        reasons.append("rotation_out_of_bounds")
    if not limits.min_distance_mm <= target.distance_mm <= limits.max_distance_mm:
        reasons.append("distance_out_of_bounds")
    if not limits.min_z_height_mm <= target.z_height_mm <= limits.max_z_height_mm:
        reasons.append("z_height_out_of_bounds")
    return reasons


def commands_for_target(target: MotionTarget) -> tuple[RobotCommand, ...]:
    commands: list[RobotCommand] = []
    if abs(target.rotation_deg) >= 0.05:
        commands.append(
            RobotCommand(
                action="baseRotate",
                body={
                    "controlType": "DEGREES",
                    "direction": "RIGHT" if target.rotation_deg > 0 else "LEFT",
                    "value": round(abs(target.rotation_deg), 3),
                    "speed": "veryslow",
                },
            )
        )
    commands.extend(
        (
            RobotCommand(
                action="controlik",
                body={"distance": round(target.distance_mm, 3), "z_height": round(target.z_height_mm, 3)},
            ),
            RobotCommand(action="gripper", body={"command": "GRAB"}),
            RobotCommand(action="calibrationvalues", body={}, role="telemetry"),
        )
    )
    return tuple(commands)


def build_motion_plan(
    *,
    operation_id: str,
    projection: CalibrationProjection,
    limits: SafetyLimits,
    corrections: PlanCorrections | None = None,
    planner_model_id: str | None = None,
) -> MotionPlan:
    baseline = MotionTarget(
        rotation_deg=float(projection.angle_deg),
        distance_mm=float(projection.distance_mm),
        z_height_mm=float(projection.z_height_mm),
    )
    raw = corrections or PlanCorrections()
    rotation_delta, rotation_clamped = _clamp(
        raw.rotation_delta_deg, -limits.max_rotation_correction_deg, limits.max_rotation_correction_deg
    )
    distance_delta, distance_clamped = _clamp(
        raw.distance_delta_mm, -limits.max_distance_correction_mm, limits.max_distance_correction_mm
    )
    z_delta, z_clamped = _clamp(raw.z_height_delta_mm, -limits.max_z_correction_mm, limits.max_z_correction_mm)
    bounded = PlanCorrections(
        rotation_delta_deg=rotation_delta,
        distance_delta_mm=distance_delta,
        z_height_delta_mm=z_delta,
        twist_position=None,
    )
    final_target = MotionTarget(
        rotation_deg=baseline.rotation_deg + bounded.rotation_delta_deg,
        distance_mm=baseline.distance_mm + bounded.distance_delta_mm,
        z_height_mm=baseline.z_height_mm + bounded.z_height_delta_mm,
    )
    reasons = _validate_target(final_target, projection, limits)
    safety = SafetyResult(
        status="rejected" if reasons else "safe",
        reasons=tuple(reasons),
        corrections_clamped=rotation_clamped or distance_clamped or z_clamped,
    )
    commands = commands_for_target(final_target) if safety.safe else ()
    return MotionPlan(
        plan_id=str(uuid.uuid4()),
        operation_id=operation_id,
        planner_mode="residual" if planner_model_id else "deterministic",
        planner_model_id=planner_model_id,
        baseline=baseline,
        corrections=bounded,
        final_target=final_target,
        commands=commands,
        safety=safety,
    )
