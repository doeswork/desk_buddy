"""Studio-owned MQTT topic catalog.

The catalog describes the topics Studio intends to use; it does not edit a
broker ACL or make a connection.  That distinction keeps documentation and
future UI affordances from pretending a robot is already connected.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Topic:
    """One fixed Studio topic and the messages it carries."""

    name: str
    title: str
    description: str
    publisher: str
    consumers: str
    groups_title: str
    message_groups: tuple[tuple[str, tuple[str, ...]], ...]


COMMANDS = Topic(
    name="{robot}/commands",
    title="Robot commands",
    description=(
        "JSON requests sent to one robot. Every request carries a sender and "
        "unique action_id; firmware is the only subscriber that executes them."
    ),
    publisher="Studio and authorized controllers",
    consumers="Desk Buddy firmware; Vision observes detect_object requests",
    groups_title="Command actions",
    message_groups=(
        ("Motion", ("servo", "gripper", "baseRotate", "controlik", "perch")),
        ("Calibration", (
            "calibrate", "calibrate_base_rotation", "calibrationvalues",
            "stencilCalibrate",
        )),
        ("Camera", ("photo", "detect_object", "detect_color", "calibrate_depth")),
        ("Firmware", ("ota_update",)),
    ),
)

EVENTS = Topic(
    name="{robot}/events",
    title="Robot events",
    description="JSON lifecycle and diagnostic output from firmware.",
    publisher="Desk Buddy firmware",
    consumers="Studio, Vision services, and authorized controllers",
    groups_title="Event kinds",
    message_groups=(("Lifecycle", (
        "ready", "in_progress", "progress", "completed", "failed", "debug",
    )),),
)

PHOTOS = Topic(
    name="{robot}/photos",
    title="Robot photos",
    description=(
        "Binary desk_buddy.photo.v1 frames containing metadata and one raw JPEG."
    ),
    publisher="Desk Buddy firmware",
    consumers="Studio and Vision services",
    groups_title="Capture types",
    message_groups=(("Camera", (
        "photo", "detect_object", "detect_color", "calibrate_depth",
    )),),
)

VISION = Topic(
    name="{robot}/vision",
    title="Vision results",
    description="Correlated JSON inference results; never consumed as firmware commands.",
    publisher="Vision services",
    consumers="Studio and authorized controllers",
    groups_title="Result kinds",
    message_groups=(("Detection", ("detect_object completed", "detect_object failed")),),
)

HEARTBEAT = Topic(
    name="{robot}/heartbeat",
    title="Robot heartbeat",
    description="Periodic robot availability, firmware, heap, and joint telemetry.",
    publisher="Desk Buddy firmware",
    consumers="Studio and monitoring services",
    groups_title="Telemetry",
    message_groups=(("Robot state", ("firmware/OTA", "joint angles", "timestamp")),),
)


TOPICS = (COMMANDS, EVENTS, PHOTOS, VISION, HEARTBEAT)
