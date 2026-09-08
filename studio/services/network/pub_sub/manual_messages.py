"""MQTT commands sent by Studio's Manual Controller.

The page owns presentation; this module owns the firmware contract. Every
command is addressed to the selected robot's ``{user}/test`` topic and is
published through :class:`MqttClient`, whose connection authenticates with
Studio's configured MQTT credentials.
"""

from __future__ import annotations

from .client import MqttClient

SENDER = "studio"
JOINTS = ("ELBOW", "WRIST", "TWIST")
GRIPPER_COMMANDS = ("GRAB", "SOFTHOLD", "DROP")
BASE_DIRECTIONS = ("LEFT", "RIGHT")
BASE_SPEEDS = ("veryslow", "slow", "regular", "fast", "superfast")


def topic_for(robot: str) -> str:
    return f"{robot}/test"


def _send(client: MqttClient, robot: str, action: str, **fields) -> str:
    return client.publish(
        topic_for(robot),
        {"sender": SENDER, "action": action, **fields},
    )


def send_servo(client: MqttClient, robot: str, joint: str, position: int) -> str:
    """Move one joint exactly as the legacy live sliders did."""
    joint = joint.upper()
    if joint not in JOINTS:
        raise ValueError(f"unknown joint: {joint!r}")
    if not 0 <= position <= 180:
        raise ValueError("servo position must be between 0 and 180")

    # Firmware deliberately gives the legacy live controller direct-drive
    # semantics when action_id is "live". Preserve that behavior here while
    # the MQTT connection and sender remain Studio's own identity.
    return _send(
        client,
        robot,
        "servo",
        action_id="live",
        servoName=joint,
        position=int(position),
    )


def send_ik(client: MqttClient, robot: str, distance: int) -> str:
    if not 1 <= distance <= 120:
        raise ValueError("IK distance must be between 1 and 120")
    return _send(client, robot, "controlik", distance=int(distance))


def send_gripper(client: MqttClient, robot: str, command: str) -> str:
    command = command.upper()
    if command not in GRIPPER_COMMANDS:
        raise ValueError(f"unknown gripper command: {command!r}")
    return _send(client, robot, "gripper", command=command)


def send_base_steps(
    client: MqttClient,
    robot: str,
    direction: str,
    steps: int,
    speed: str = "slow",
) -> str:
    direction = direction.upper()
    speed = speed.lower()
    if direction not in BASE_DIRECTIONS:
        raise ValueError(f"unknown base direction: {direction!r}")
    if not 1 <= steps <= 216:
        raise ValueError("base steps must be between 1 and 216")
    if speed not in BASE_SPEEDS:
        raise ValueError(f"unknown base speed: {speed!r}")
    return _send(
        client,
        robot,
        "baseRotate",
        controlType="ENCODER",
        direction=direction,
        value=int(steps),
        speed=speed,
    )


def send_base_home(
    client: MqttClient,
    robot: str,
    *,
    direction: str = "RIGHT",
    speed: str = "veryslow",
) -> str:
    direction = direction.upper()
    speed = speed.lower()
    if direction not in BASE_DIRECTIONS:
        raise ValueError(f"unknown base direction: {direction!r}")
    if speed not in BASE_SPEEDS:
        raise ValueError(f"unknown base speed: {speed!r}")
    return _send(
        client,
        robot,
        "baseRotate",
        controlType="HOME",
        direction=direction,
        speed=speed,
    )


def send_perch(client: MqttClient, robot: str) -> str:
    return _send(client, robot, "perch")


def send_photo(client: MqttClient, robot: str) -> str:
    return _send(client, robot, "photo")
