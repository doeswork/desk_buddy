"""Reusable vision-to-firmware IK planning and execution."""

from __future__ import annotations

from .calibration import CalibrationProjection, build_grid, find_zone, project, reference_point
from .calibration_photo import CalibrationResult, calibrate, detect_calibration_points
from .execution import (
    Dispatch,
    ExecutionResult,
    IKControl,
    IKLimits,
    RobotCommand,
    commands_for_target,
    validate_target,
)
from .strategies import (
    DeterministicGridStrategy,
    IKPlanningContext,
    LearnedModelCompatibility,
    target_from_prediction,
)

__all__ = [
    "CalibrationProjection",
    "CalibrationResult",
    "DeterministicGridStrategy",
    "Dispatch",
    "ExecutionResult",
    "IKControl",
    "IKLimits",
    "IKPlanningContext",
    "LearnedModelCompatibility",
    "RobotCommand",
    "build_grid",
    "calibrate",
    "commands_for_target",
    "find_zone",
    "detect_calibration_points",
    "project",
    "reference_point",
    "target_from_prediction",
    "validate_target",
]
