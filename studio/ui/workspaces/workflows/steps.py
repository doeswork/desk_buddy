"""The firmware action vocabulary, as the buttons on the Workflows toolbar.

`firmware/README.md` is the contract. This module restates the part a workflow
can use so Studio can offer it as buttons: a person building a routine should
not have to remember that base movement is `baseRotate` with a `controlType`,
or that a servo takes `servoName` rather than `joint`.

Templates carry real, valid defaults rather than empty placeholders. Inserting
one gives you a step that would run as-is, which makes the palette a starting
point to edit instead of a form to fill in.

Steps are stored under `subject` (the Rails-era key the workflow file uses),
while every other field name matches the firmware message exactly. Nothing
executes workflows yet; when a runner arrives it maps `subject` to `action`.

Lives beside the toolbar it fills rather than in `models/config/`, where it
sat originally: everything there is persisted state backed by a Store, and
this is a hardcoded list of buttons with exactly one consumer. If a workflow
*runner* ever needs the same table to validate what it is about to send, that
is the moment to move it back down — not before.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Firmware enumerations, mirrored from firmware/README.md.
JOINTS = ("ELBOW", "WRIST", "TWIST")
GRIPPER_COMMANDS = ("GRAB", "SOFTHOLD", "DROP")
DIRECTIONS = ("LEFT", "RIGHT")
SPEEDS = ("veryslow", "slow", "regular", "fast", "superfast")

# 216 firmware steps is one full base rotation (108-tooth gear, 18-tooth drive).
STEPS_PER_REVOLUTION = 216

# The calibration_type values `calibrate` accepts, split by what they take:
# a hover point carries `distance`, a perch value carries `value`.
HOVER_TYPES = (
    "hover_over_min", "hover_over_mid", "hover_over_max",
    "hover_min_120", "hover_mid_120", "hover_max_120",
)
PERCH_ANGLE_TYPES = (
    "perch_elbow_angle", "perch_wrist_angle", "perch_twist_angle",
)
PERCH_REACH_TYPES = ("perch_min", "perch_mid", "perch_max")

STENCIL_COMMANDS = (
    "START", "RUN_POINT", "ADJUST", "ADJUST_PREVIOUS", "STATUS",
    "CANCEL", "CLEAR",
)


@dataclass(frozen=True)
class StepTemplate:
    """One insertable step: what it does, and the JSON it produces."""

    key: str
    label: str
    subject: str
    summary: str
    fields: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_step(self) -> dict[str, Any]:
        """A fresh step dictionary; callers may edit it freely."""
        return {"subject": self.subject, **self.fields}


@dataclass(frozen=True)
class StepGroup:
    label: str
    templates: tuple[StepTemplate, ...]


ARM = StepGroup(
    "Arm",
    (
        StepTemplate(
            "servo_elbow", "Elbow", "servo",
            "Set the elbow servo angle.",
            {"servoName": "ELBOW", "position": 90, "speed": 10},
            "position 0–180; speed is delay per degree in ms.",
        ),
        StepTemplate(
            "servo_wrist", "Wrist", "servo",
            "Set the wrist servo angle.",
            {"servoName": "WRIST", "position": 90, "speed": 10},
            "position 0–180; speed is delay per degree in ms.",
        ),
        StepTemplate(
            "servo_twist", "Twist", "servo",
            "Set the twist servo angle.",
            {"servoName": "TWIST", "position": 90, "speed": 10},
            "position 0–180; speed is delay per degree in ms.",
        ),
        StepTemplate(
            "controlik", "Reach (IK)", "controlik",
            "Move the arm to a distance using inverse kinematics.",
            {"distance": 85.0, "z_height": 0.0},
            "distance in mm; z_height 0–50, nonzero needs hover_*_120 "
            "calibration. Requests outside the trapezoid fail.",
        ),
        StepTemplate(
            "perch", "Perch", "perch",
            "Return the arm to its stored perch pose.",
            {},
            "Uses the saved perch calibration angles.",
        ),
    ),
)

GRIPPER = StepGroup(
    "Gripper",
    (
        StepTemplate(
            "gripper_grab", "Grab", "gripper",
            "Close the gripper to grab.",
            {"command": "GRAB"},
        ),
        StepTemplate(
            "gripper_softhold", "Soft hold", "gripper",
            "Hold gently, without a full grab.",
            {"command": "SOFTHOLD"},
        ),
        StepTemplate(
            "gripper_drop", "Drop", "gripper",
            "Open the gripper to release.",
            {"command": "DROP"},
        ),
        StepTemplate(
            "gripper_position", "Position", "gripper",
            "Set an exact gripper angle.",
            {"position": 120, "speed": 10},
            "position 0–180; speed is delay per degree in ms.",
        ),
    ),
)

BASE = StepGroup(
    "Base",
    (
        StepTemplate(
            "base_steps", "Rotate steps", "baseRotate",
            "Rotate the base by firmware steps.",
            {
                "controlType": "ENCODER",
                "direction": "RIGHT",
                "value": 10,
                "speed": "slow",
            },
            f"{STEPS_PER_REVOLUTION} steps is one full rotation; "
            "2 steps is about one tooth. Omitted speed defaults to slow.",
        ),
        StepTemplate(
            "base_home", "Home to north", "baseRotate",
            "Home the base to true north.",
            {"controlType": "HOME", "direction": "RIGHT", "speed": "veryslow"},
        ),
        StepTemplate(
            "base_status", "Base status", "baseRotate",
            "Ask for the base rotation status.",
            {"controlType": "STATUS"},
            "Replies with a base_rotation object.",
        ),
    ),
)

VISION = StepGroup(
    "Vision",
    (
        StepTemplate(
            "photo", "Photo", "photo",
            "Take a photo.",
            {},
        ),
        StepTemplate(
            "detect_object", "Detect object", "detect_object",
            "Look for objects matching a phrase.",
            {"phrase": ["red cup"]},
            "phrase is a list of things to look for.",
        ),
        StepTemplate(
            "detect_color", "Detect color", "detect_color",
            "Run color detection.",
            {},
        ),
        StepTemplate(
            "calibrate_depth", "Depth photo", "calibrate_depth",
            "Capture a frame for depth calibration.",
            {},
            "A photo action: replies in_progress then sends the frame, "
            "with no completed message.",
        ),
    ),
)

# Calibration writes. These change what the robot believes about itself, so a
# workflow containing one is a setup routine rather than a movement — which is
# why they are grouped apart from Arm and Base rather than beside the moves
# that use them.
CALIBRATION = StepGroup(
    "Calibration",
    (
        StepTemplate(
            "calibration_values", "Read values", "calibrationvalues",
            "Ask the robot for every stored calibration value.",
            {},
            "Replies with a calibrationvalues object. Unsaved keys come "
            "back null, so this is the way to see what is missing.",
        ),
        StepTemplate(
            "calibrate_hover", "Save hover point", "calibrate",
            "Record a reach landmark at the arm's current angles.",
            {"calibration_type": "hover_over_mid", "distance": 60},
            "calibration_type is one of hover_over_min/mid/max (table "
            "level) or hover_min_120/mid_120/max_120 (50 mm up). distance "
            "is how far out the gripper is, in mm. Keep min < mid < max.",
        ),
        StepTemplate(
            "calibrate_perch_angle", "Save perch angle", "calibrate",
            "Record one perch joint angle.",
            {"calibration_type": "perch_elbow_angle", "value": 125},
            "calibration_type is perch_elbow_angle, perch_wrist_angle or "
            "perch_twist_angle. value is the servo angle, 0–180.",
        ),
        StepTemplate(
            "calibrate_perch_reach", "Save perch reach", "calibrate",
            "Record one perch distance landmark.",
            {"calibration_type": "perch_mid", "value": 50},
            "calibration_type is perch_min, perch_mid or perch_max. "
            "value is a distance in mm.",
        ),
        StepTemplate(
            "calibrate_base_rotation", "Profile base rotation", "calibrate_base_rotation",
            "Measure the base's rotation profile.",
            {"neutralServoAngle": 93},
            "Runs for several minutes and publishes progress throughout. "
            "neutralServoAngle is optional, 0–180.",
        ),
    ),
)

STENCIL = StepGroup(
    "Stencil",
    (
        StepTemplate(
            "stencil_start", "Start", "stencilCalibrate",
            "Begin a stencil calibration session.",
            {"command": "START"},
            "Moves to perch, homes the base to true north, then checks the "
            "base is ready.",
        ),
        StepTemplate(
            "stencil_run_point", "Run point", "stencilCalibrate",
            "Attempt the session's next stencil point.",
            {"command": "RUN_POINT"},
            "A miss replies completed with phase needs_adjustment — nudge "
            "with Adjust, then run the same point again.",
        ),
        StepTemplate(
            "stencil_adjust", "Adjust", "stencilCalibrate",
            "Nudge the current point before retrying it.",
            {"command": "ADJUST", "rotationNudgeDegrees": 2.0,
             "distanceNudgeMm": -3.0},
            "Use ADJUST_PREVIOUS instead to correct the point just "
            "completed.",
        ),
        StepTemplate(
            "stencil_status", "Status", "stencilCalibrate",
            "Ask where the stencil session is up to.",
            {"command": "STATUS"},
            "Replies with a stencil_calibration object.",
        ),
        StepTemplate(
            "stencil_cancel", "Cancel", "stencilCalibrate",
            "Stop the session, keeping what it has saved.",
            {"command": "CANCEL"},
            "CLEAR discards the saved offsets instead.",
        ),
    ),
)

SYSTEM = StepGroup(
    "System",
    (
        StepTemplate(
            "ota_update", "Firmware update", "ota_update",
            "Flash new firmware from a release URL.",
            {
                "url": "https://github.com/user/repo/releases/download/"
                       "v1.0.0/firmware.bin",
                "version": "v1.0.0",
            },
            "The robot reboots on success and never replies. Add token for "
            "a private repo, and sha256 to verify the download.",
        ),
    ),
)

CUSTOM = StepGroup(
    "Custom",
    (
        StepTemplate(
            "custom", "Custom step", "custom_step",
            "A step of your own, with any properties you like.",
            {"note": "Workflows are allowed to be special."},
            "Any subject and any JSON properties are preserved.",
        ),
    ),
)

GROUPS: tuple[StepGroup, ...] = (
    ARM, GRIPPER, BASE, VISION, CALIBRATION, STENCIL, SYSTEM, CUSTOM,
)


def all_templates() -> tuple[StepTemplate, ...]:
    return tuple(t for group in GROUPS for t in group.templates)


def find(key: str) -> StepTemplate | None:
    for template in all_templates():
        if template.key == key:
            return template
    return None


__all__ = [
    "DIRECTIONS",
    "GRIPPER_COMMANDS",
    "GROUPS",
    "HOVER_TYPES",
    "JOINTS",
    "PERCH_ANGLE_TYPES",
    "PERCH_REACH_TYPES",
    "SPEEDS",
    "STENCIL_COMMANDS",
    "STEPS_PER_REVOLUTION",
    "StepGroup",
    "StepTemplate",
    "all_templates",
    "find",
]
