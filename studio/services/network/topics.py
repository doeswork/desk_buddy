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
    message_groups: tuple[tuple[str, tuple[str, ...]], ...]


CALIBRATIONS = Topic(
    name="calibrations",
    title="Calibrations",
    description=(
        "Calibration commands and their results. Every message is sent by "
        "Studio with sender: studio and a unique action_id so firmware and "
        "Vision responses can be correlated."
    ),
    publisher="Studio (studio)",
    consumers="Desk Buddy firmware and Vision services",
    message_groups=(
        ("Base + Perch", ("calibrate_base_rotation", "baseRotate", "perch", "calibrate")),
        ("Inverse Kinematics", ("controlik", "calibrate")),
        ("Visual", ("calibrate_depth",)),
        ("Reach and Grab", ("detect_object",)),
        ("Stencil", ("stencilCalibrate",)),
    ),
)


TOPICS = (CALIBRATIONS,)
