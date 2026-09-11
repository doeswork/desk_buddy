"""The records Studio keeps, and the rules about their shape.

    config/     small records backed by readable JSON files
    data/       append-heavy history backed by SQLite

A model owns three things: what a record *is* (the dataclass), what makes one
valid, and how it is read back and written. It owns no side effects on the
world outside its file — creating a Unix account, writing Mosquitto's own
config, signalling a running broker are all `studio/services/` work. The
split is what lets a record survive independently of whether the thing it
describes is currently running.
"""

from __future__ import annotations

from .config import (
    Calibration,
    Calibrations,
    MqttUser,
    Robot,
    Robots,
    RobotProfile,
    RobotProfiles,
    TOPIC_RULE,
    Workflow,
    Workflows,
    calibrations,
    default_topics,
    robots,
    profiles,
    users,
    validate_topic,
    workflows,
)
from .data import AppError, MqttMessage, app_errors, mqtt_messages

__all__ = [
    "Calibration",
    "Calibrations",
    "MqttUser",
    "MqttMessage",
    "AppError",
    "Robot",
    "Robots",
    "RobotProfile",
    "RobotProfiles",
    "TOPIC_RULE",
    "Workflow",
    "Workflows",
    "calibrations",
    "default_topics",
    "robots",
    "profiles",
    "users",
    "mqtt_messages",
    "app_errors",
    "validate_topic",
    "workflows",
]
