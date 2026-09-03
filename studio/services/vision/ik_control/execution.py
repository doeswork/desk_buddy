"""Safety validation and non-retrying firmware command sequencing."""

from __future__ import annotations

import time
import math
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..contracts import IKTargetV1


@dataclass(frozen=True)
class IKLimits:
    max_rotation_deg: float = 45.0
    min_distance_mm: float = 0.0
    max_distance_mm: float = 180.0
    min_z_height_mm: float = -30.0
    max_z_height_mm: float = 120.0


def validate_target(target: IKTargetV1, limits: IKLimits) -> IKTargetV1:
    reasons = list(target.rejection_reasons)
    if not target.accepted and not reasons:
        reasons.append("strategy_rejected")
    if not math.isfinite(target.rotation_deg):
        reasons.append("rotation_not_finite")
    elif abs(target.rotation_deg) > limits.max_rotation_deg:
        reasons.append("rotation_out_of_bounds")
    if not math.isfinite(target.distance_mm):
        reasons.append("distance_not_finite")
    elif not limits.min_distance_mm <= target.distance_mm <= limits.max_distance_mm:
        reasons.append("distance_out_of_bounds")
    if not math.isfinite(target.z_height_mm):
        reasons.append("z_height_not_finite")
    elif not limits.min_z_height_mm <= target.z_height_mm <= limits.max_z_height_mm:
        reasons.append("z_height_out_of_bounds")
    return IKTargetV1(
        rotation_deg=target.rotation_deg,
        distance_mm=target.distance_mm,
        z_height_mm=target.z_height_mm,
        strategy_id=target.strategy_id,
        model_id=target.model_id,
        accepted=not reasons,
        rejection_reasons=tuple(dict.fromkeys(reasons)),
    )


@dataclass(frozen=True)
class RobotCommand:
    action: str
    body: dict[str, Any]
    role: str = "motion"


def commands_for_target(
    target: IKTargetV1,
    *,
    grab: bool = False,
    telemetry: bool = False,
) -> tuple[RobotCommand, ...]:
    if not target.accepted:
        raise ValueError("cannot build commands for a rejected IK target")
    commands = []
    if abs(target.rotation_deg) >= 0.05:
        commands.append(
            RobotCommand(
                "baseRotate",
                {
                    "controlType": "DEGREES",
                    "direction": "RIGHT" if target.rotation_deg > 0 else "LEFT",
                    "value": round(abs(target.rotation_deg), 3),
                    "speed": "veryslow",
                },
            )
        )
    commands.append(
        RobotCommand(
            "controlik",
            {"distance": round(target.distance_mm, 3), "z_height": round(target.z_height_mm, 3)},
        )
    )
    if grab:
        commands.append(RobotCommand("gripper", {"command": "GRAB"}))
    if telemetry:
        commands.append(RobotCommand("calibrationvalues", {}, role="telemetry"))
    return tuple(commands)


@dataclass(frozen=True)
class Dispatch:
    sequence_id: str
    operation_id: str
    topic: str
    step_index: int
    action_id: str
    role: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ExecutionResult:
    sequence_id: str
    operation_id: str
    success: bool
    error: str | None
    warning: str | None
    failed_step: int | None
    events: tuple[dict[str, Any], ...]


@dataclass
class _Sequence:
    sequence_id: str
    operation_id: str
    topic: str
    commands: tuple[RobotCommand, ...]
    index: int = 0
    action_id: str = ""
    deadline: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)


class IKControl:
    """The only code that translates an IK target into firmware messages."""

    def __init__(
        self,
        publish: Callable[[str, Mapping[str, Any], int], bool],
        *,
        limits: IKLimits | None = None,
        timeout_seconds: float = 30.0,
        sender: str = "ai_server",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.publish = publish
        self.limits = limits or IKLimits()
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.sender = sender
        self.clock = clock
        self._by_topic: dict[str, _Sequence] = {}
        self._topic_by_action: dict[str, str] = {}
        self._lock = threading.RLock()
        self.on_dispatch: Callable[[Dispatch], None] | None = None
        self.on_terminal: Callable[[ExecutionResult], None] | None = None

    def execute(
        self,
        *,
        topic: str,
        operation_id: str,
        target: IKTargetV1,
        grab: bool = False,
        telemetry: bool = False,
    ) -> Dispatch | ExecutionResult:
        with self._lock:
            checked = validate_target(target, self.limits)
            if not checked.accepted:
                return ExecutionResult("", operation_id, False, ",".join(checked.rejection_reasons), None, None, ())
            if topic in self._by_topic:
                return ExecutionResult("", operation_id, False, "robot_busy", None, None, ())
            state = _Sequence(uuid.uuid4().hex[:12], operation_id, topic, commands_for_target(checked, grab=grab, telemetry=telemetry))
            self._by_topic[topic] = state
            return self._send(state, 0)

    def handle_firmware(self, topic: str, body: Mapping[str, Any]) -> bool:
        with self._lock:
            if str(body.get("sender") or "").lower() != "firmware":
                return False
            action_id = str(body.get("action_id") or "")
            status = str(body.get("status") or "").lower()
            if self._topic_by_action.get(action_id) != topic or status not in {"in_progress", "completed", "failed"}:
                return False
            state = self._by_topic.get(topic)
            if state is None or state.action_id != action_id:
                return False
            if status == "in_progress":
                return True
            command = state.commands[state.index]
            state.events.append(
                {
                    "step_index": state.index + 1,
                    "action_id": action_id,
                    "action": command.action,
                    "role": command.role,
                    "status": status,
                    "response": dict(body),
                }
            )
            self._topic_by_action.pop(action_id, None)
            if status == "completed":
                if state.index + 1 < len(state.commands):
                    self._send(state, state.index + 1)
                else:
                    self._finish(state, True, None, None)
                return True
            if command.role == "telemetry":
                self._finish(state, True, None, "telemetry_failed")
            else:
                self._finish(state, False, "robot_command_failed", None)
            return True

    def expire(self) -> tuple[ExecutionResult, ...]:
        with self._lock:
            now = self.clock()
            expired = [state for state in self._by_topic.values() if state.deadline <= now]
            return tuple(self._finish(state, False, "robot_command_timeout", None) for state in expired)

    def _send(self, state: _Sequence, index: int) -> Dispatch:
        command = state.commands[index]
        action_id = f"rg-{state.sequence_id}-{index + 1}"
        payload = {"sender": self.sender, "action_id": action_id, "action": command.action, **command.body}
        state.index = index
        state.action_id = action_id
        state.deadline = self.clock() + self.timeout_seconds
        self._topic_by_action[action_id] = state.topic
        dispatch = Dispatch(state.sequence_id, state.operation_id, state.topic, index + 1, action_id, command.role, payload)
        if not self.publish(state.topic, payload, 0):
            state.events.append({"step_index": index + 1, "action_id": action_id, "status": "publish_failed"})
            self._finish(state, False, "robot_command_publish_failed", None)
        elif self.on_dispatch:
            self.on_dispatch(dispatch)
        return dispatch

    def _finish(self, state: _Sequence, success: bool, error: str | None, warning: str | None) -> ExecutionResult:
        self._topic_by_action.pop(state.action_id, None)
        self._by_topic.pop(state.topic, None)
        result = ExecutionResult(
            state.sequence_id,
            state.operation_id,
            success,
            error,
            warning,
            state.index + 1 if error else None,
            tuple(state.events),
        )
        if self.on_terminal:
            self.on_terminal(result)
        return result
