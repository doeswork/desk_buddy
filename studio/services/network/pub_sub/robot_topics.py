"""Canonical MQTT topics for one Desk Buddy robot.

Commands, lifecycle events, binary photos, Vision results, and telemetry are
kept on separate channels so consumers never have to infer a payload type by
trying to decode it.
"""

from __future__ import annotations


def _root(robot: str) -> str:
    root = str(robot).strip().strip("/")
    if not root:
        raise ValueError("a robot topic root is required")
    return root


def command_topic(robot: str) -> str:
    return f"{_root(robot)}/commands"


def event_topic(robot: str) -> str:
    return f"{_root(robot)}/events"


def photo_topic(robot: str) -> str:
    return f"{_root(robot)}/photos"


def vision_topic(robot: str) -> str:
    return f"{_root(robot)}/vision"


def heartbeat_topic(robot: str) -> str:
    return f"{_root(robot)}/heartbeat"
