"""Vision-strategy implementations that all produce IKTargetV1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..contracts import DetectionV1, IKPredictionV1, IKTargetV1, VisionObservationV1
from .calibration import project


@dataclass(frozen=True)
class IKPlanningContext:
    """Provider-neutral inputs available to an IK strategy.

    Detection is the only universal input.  Depth-rich observations remain a
    separate stable contract and are present only for strategies that request
    them; callers must never synthesize depth values to satisfy an interface.
    """

    capture_id: str
    robot_id: str
    prompt: str
    detection: DetectionV1
    detector_model_id: str
    detector_model_version: str
    calibration_profile_id: str = ""
    depth_observation: VisionObservationV1 | None = None


class DeterministicGridStrategy:
    strategy_id = "nine-point-grid.v1"
    required_inputs = frozenset({"detection"})

    def __init__(self, grid: Mapping[str, Any], *, reference_y_fraction: float = 0.1) -> None:
        self.grid = dict(grid)
        self.reference_y_fraction = reference_y_fraction

    def plan(self, observation: IKPlanningContext) -> IKTargetV1:
        projection = project(
            observation.detection.bbox_px,
            self.grid,
            reference_y_fraction=self.reference_y_fraction,
        )
        if not projection.calibrated:
            return IKTargetV1(
                0.0,
                0.0,
                0.0,
                self.strategy_id,
                accepted=False,
                rejection_reasons=("detection_outside_calibrated_workspace",),
            )
        return IKTargetV1(
            rotation_deg=projection.angle_deg,
            distance_mm=projection.distance_mm,
            z_height_mm=projection.z_height_mm,
            strategy_id=self.strategy_id,
        )


@dataclass(frozen=True)
class LearnedModelCompatibility:
    model_id: str
    detector_model_id: str
    detector_model_version: str
    depth_model_id: str
    depth_model_version: str
    feature_schema: str = "vision-features.v1"


def target_from_prediction(
    observation: VisionObservationV1,
    prediction: IKPredictionV1,
    compatibility: LearnedModelCompatibility,
) -> IKTargetV1:
    mismatches = []
    for name, actual, expected in (
        ("detector_model", observation.detector_model_id, compatibility.detector_model_id),
        ("detector_version", observation.detector_model_version, compatibility.detector_model_version),
        ("depth_model", observation.depth_model_id, compatibility.depth_model_id),
        ("depth_version", observation.depth_model_version, compatibility.depth_model_version),
    ):
        if actual != expected:
            mismatches.append(f"{name}_mismatch")
    if prediction.model_id != compatibility.model_id:
        mismatches.append("prediction_model_mismatch")
    return IKTargetV1(
        rotation_deg=prediction.rotation_deg,
        distance_mm=prediction.distance_mm,
        z_height_mm=prediction.z_height_mm,
        strategy_id="learned-full-target.v1",
        model_id=prediction.model_id,
        accepted=not mismatches,
        rejection_reasons=tuple(mismatches),
    )
