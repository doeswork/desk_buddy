"""The firmware action vocabulary, as insertable workflow step templates.

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

GROUPS: tuple[StepGroup, ...] = (ARM, GRIPPER, BASE, VISION, CUSTOM)


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
    "JOINTS",
    "SPEEDS",
    "STEPS_PER_REVOLUTION",
    "StepGroup",
    "StepTemplate",
    "all_templates",
    "find",
]
