from __future__ import annotations

import unittest

from tests.support import ROOT

from desk_buddy_vision_protocol import PlanCorrections
from desk_buddy_vision.planning import CalibrationProjection, SafetyLimits, build_motion_plan
from desk_buddy_vision.sequencer import RobotCommandSequencer


class PlanningSequencerTests(unittest.TestCase):
    def test_deterministic_and_residual_share_command_shape(self):
        projection = CalibrationProjection(15.0, 80.0, 2.0, "a1-d1", True, False)
        deterministic = build_motion_plan(operation_id="op-1", projection=projection, limits=SafetyLimits())
        residual = build_motion_plan(
            operation_id="op-2",
            projection=projection,
            limits=SafetyLimits(),
            corrections=PlanCorrections(2.0, -3.0, 1.0),
            planner_model_id="residual-1",
        )
        self.assertEqual([command.action for command in deterministic.commands], [command.action for command in residual.commands])
        self.assertEqual(residual.final_target.distance_mm, 77.0)

    def test_corrections_are_clamped_then_revalidated(self):
        plan = build_motion_plan(
            operation_id="op-1",
            projection=CalibrationProjection(40.0, 170.0, 0.0, "edge", True, False),
            limits=SafetyLimits(max_rotation_correction_deg=5, max_distance_correction_mm=5),
            corrections=PlanCorrections(100.0, 100.0, 0.0),
            planner_model_id="model",
        )
        self.assertTrue(plan.safety.corrections_clamped)
        self.assertTrue(plan.safety.safe)
        self.assertEqual(plan.final_target.rotation_deg, 45.0)

    def test_extrapolated_motion_is_rejected_by_default(self):
        plan = build_motion_plan(
            operation_id="op-1",
            projection=CalibrationProjection(30.0, 120.0, 0.0, "edge", False, True),
            limits=SafetyLimits(),
        )
        self.assertFalse(plan.safety.safe)
        self.assertIn("extrapolated_motion_disabled", plan.safety.reasons)

    def test_sequencer_waits_for_matching_completion_and_never_retries(self):
        now = 100.0
        plan = build_motion_plan(
            operation_id="op-1",
            projection=CalibrationProjection(10.0, 80.0, 0.0, "zone", True, False),
            limits=SafetyLimits(),
        )
        sequencer = RobotCommandSequencer(
            timeout_seconds=30,
            clock=lambda: now,
            token_factory=lambda: "fixed",
        )
        first = sequencer.start(topic="robot/test", operation_id="op-1", commands=plan.commands).dispatch
        ignored = sequencer.handle_firmware_message(
            "robot/test", {"sender": "firmware", "action_id": "wrong", "status": "completed"}
        )
        self.assertFalse(ignored.consumed)
        second = sequencer.handle_firmware_message(
            "robot/test", {"sender": "firmware", "action_id": first.command_action_id, "status": "completed"}
        ).dispatch
        self.assertEqual(second.payload["action"], "controlik")
        timeout = sequencer.expire(now=131.0)[0].terminal
        self.assertEqual(timeout.error, "robot_command_timeout")
        self.assertFalse(sequencer.is_busy("robot/test"))


if __name__ == "__main__":
    unittest.main()

