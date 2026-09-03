"""The records Studio keeps, and the rules about their shape.

    mqtt_users.py     broker accounts: name, password, topics
    mqtt_topics.py    what a topic filter is, and what a new account starts with
    calibrations.py   a calibration result per robot

A model owns three things: what a record *is* (the dataclass), what makes one
valid, and how it is read back and written. It owns no side effects on the
world outside its file — creating a Unix account, writing Mosquitto's own
config, signalling a running broker are all `studio/services/` work. The
split is what lets a record survive independently of whether the thing it
describes is currently running.
"""

from __future__ import annotations

from .mqtt_users import MqttUser, users
from .mqtt_topics import TOPIC_RULE, default_topics, validate_topic

__all__ = [
    "MqttUser",
    "TOPIC_RULE",
    "default_topics",
    "users",
    "validate_topic",
]
