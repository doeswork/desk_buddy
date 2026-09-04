"""Calibration command envelopes, per MQTT_SPEC.md §5.

The UI layer should never assemble one of these by hand — the field names and
which ones are required are a firmware contract, not a page's business. Each
function here builds one `calibrate`-family request and publishes it,
returning the action_id the caller subscribes on for the reply.

    {mqtt_user}/test    every calibration request and response, per §1
"""

from __future__ import annotations

from .client import MqttClient

SENDER = "calibration_tool"


def topic_for(robot: str) -> str:
    return f"{robot}/test"


def _send(client: MqttClient, robot: str, action: str, **fields) -> str:
    payload = {"sender": SENDER, "action": action, **fields}
    return client.publish(topic_for(robot), payload)


# ---- Base + Perch (§4.3, §5.1) --------------------------------------------

PERCH_TYPES = {
    "elbow": "perch_elbow_angle",
    "wrist": "perch_wrist_angle",
    "twist": "perch_twist_angle",
    "min": "perch_min",
    "mid": "perch_mid",
    "max": "perch_max",
}


def send_perch_value(client: MqttClient, robot: str, field: str, value: float) -> str:
    """One perch value: elbow/wrist/twist angle, or min/mid/max reach."""
    calibration_type = PERCH_TYPES[field]
    return _send(
        client, robot, "calibrate",
        calibration_type=calibration_type, value=value,
    )


def send_base_rotation_profile(
    client: MqttClient, robot: str, *, neutral_servo_angle: int | None = None
) -> str:
    """Starts the base-rotation profile run. Publishes progress until terminal."""
    fields = {}
    if neutral_servo_angle is not None:
        fields["neutralServoAngle"] = neutral_servo_angle
    return _send(client, robot, "calibrate_base_rotation", **fields)


# ---- Inverse Kinematics: hover points (§5.1) ------------------------------

HOVER_TYPES = (
    "hover_over_min", "hover_over_mid", "hover_over_max",
    "hover_min_120", "hover_mid_120", "hover_max_120",
)


def send_hover_point(
    client: MqttClient, robot: str, calibration_type: str, distance: float,
    *, elbow: float | None = None, wrist: float | None = None,
    twist: float | None = None,
) -> str:
    if calibration_type not in HOVER_TYPES:
        raise ValueError(f"unknown hover calibration_type: {calibration_type!r}")

    fields = {"distance": distance}
    if elbow is not None:
        fields["ELBOW"] = elbow
    if wrist is not None:
        fields["WRIST"] = wrist
    if twist is not None:
        fields["TWIST"] = twist
    return _send(client, robot, "calibrate", calibration_type=calibration_type, **fields)


# ---- Visual / Reach and Grab: photo actions (§6) --------------------------
# These trigger a capture rather than a `calibrate` write; the firmware's
# only reply is an in_progress/log:"sent" pair plus a binary photo frame, not
# a completed/failed calibration result. Full frame decoding is out of scope
# for this pass — see PLAN.md — so these only confirm the request went out.

def send_calibrate_depth(client: MqttClient, robot: str) -> str:
    return _send(client, robot, "calibrate_depth")


def send_detect_object(client: MqttClient, robot: str, *, phrase=None) -> str:
    fields = {} if phrase is None else {"phrase": phrase}
    return _send(client, robot, "detect_object", **fields)


# ---- Stencil (§5.3) --------------------------------------------------------

def send_stencil_command(
    client: MqttClient, robot: str, command: str,
    *, rotation_nudge_degrees: float | None = None,
    distance_nudge_mm: float | None = None,
) -> str:
    """One stencilCalibrate command: START, RUN_POINT, ADJUST, ADJUST_PREVIOUS,
    STATUS, CANCEL, or CLEAR."""
    fields = {}
    if rotation_nudge_degrees is not None:
        fields["rotationNudgeDegrees"] = rotation_nudge_degrees
    if distance_nudge_mm is not None:
        fields["distanceNudgeMm"] = distance_nudge_mm
    return _send(client, robot, "stencilCalibrate", command=command, **fields)
