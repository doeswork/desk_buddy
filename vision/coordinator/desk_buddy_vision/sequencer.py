from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class RobotCommand:
    action: str
    body: dict[str, Any]
    role: str = "motion"


@dataclass(frozen=True)
class RobotDispatch:
    sequence_id: str
    operation_id: str
    topic: str
    step_index: int
    role: str
    command_action_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class SequenceTerminal:
    sequence_id: str
    operation_id: str
    topic: str
    success: bool
    error: str | None
    warning: str | None
    failed_step: int | None
    failed_action: str | None
    motion_steps_completed: int
    telemetry_status: str
    events: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class SequenceTransition:
    consumed: bool = False
    dispatch: RobotDispatch | None = None
    terminal: SequenceTerminal | None = None


@dataclass
class _Sequence:
    sequence_id: str
    operation_id: str
    topic: str
    commands: tuple[RobotCommand, ...]
    current_index: int = 0
    current_action_id: str = ""
    deadline: float = 0.0
    motion_steps_completed: int = 0
    failure_error: str | None = None
    failed_step: int | None = None
    failed_action: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)


class RobotCommandSequencer:
    """One non-retrying physical command sequence per firmware topic."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self._clock = clock
        self._token_factory = token_factory or (lambda: uuid.uuid4().hex[:12])
        self._by_topic: dict[str, _Sequence] = {}
        self._topic_by_action: dict[str, str] = {}
        self._lock = threading.RLock()

    def is_busy(self, topic: str) -> bool:
        with self._lock:
            return topic in self._by_topic

    def start(
        self,
        *,
        topic: str,
        operation_id: str,
        commands: Sequence[RobotCommand],
    ) -> SequenceTransition:
        validated = validate_reach_and_grab_commands(commands)
        with self._lock:
            if topic in self._by_topic:
                return SequenceTransition(
                    consumed=True,
                    terminal=SequenceTerminal(
                        sequence_id="",
                        operation_id=operation_id,
                        topic=topic,
                        success=False,
                        error="robot_busy",
                        warning=None,
                        failed_step=None,
                        failed_action=None,
                        motion_steps_completed=0,
                        telemetry_status="not_started",
                        events=(),
                    ),
                )
            state = _Sequence(
                sequence_id=self._token_factory(),
                operation_id=operation_id,
                topic=topic,
                commands=validated,
            )
            self._by_topic[topic] = state
            return SequenceTransition(consumed=True, dispatch=self._dispatch(state, 0))

    def handle_firmware_message(self, topic: str, body: Mapping[str, Any]) -> SequenceTransition:
        if str(body.get("sender") or "").lower() != "firmware":
            return SequenceTransition()
        action_id = str(body.get("action_id") or "")
        status = str(body.get("status") or "").lower()
        if not action_id or status not in {"in_progress", "completed", "failed"}:
            return SequenceTransition()
        with self._lock:
            if self._topic_by_action.get(action_id) != topic:
                return SequenceTransition()
            state = self._by_topic.get(topic)
            if state is None or state.current_action_id != action_id:
                return SequenceTransition()
            if status == "in_progress":
                return SequenceTransition(consumed=True)

            command = state.commands[state.current_index]
            state.events.append(
                {
                    "step_index": state.current_index + 1,
                    "role": command.role,
                    "action_id": action_id,
                    "action": command.action,
                    "status": status,
                    "response": dict(body),
                }
            )
            self._topic_by_action.pop(action_id, None)
            if status == "completed":
                if command.role == "motion":
                    state.motion_steps_completed += 1
                next_index = state.current_index + 1
                if next_index < len(state.commands):
                    return SequenceTransition(consumed=True, dispatch=self._dispatch(state, next_index))
                return SequenceTransition(consumed=True, terminal=self._finish(state, telemetry_status="completed"))

            if command.role == "telemetry":
                return SequenceTransition(
                    consumed=True,
                    terminal=self._finish(state, telemetry_status="failed", warning="telemetry_failed"),
                )
            state.failure_error = "robot_command_failed"
            state.failed_step = state.current_index + 1
            state.failed_action = command.action
            telemetry_index = self._telemetry_index(state)
            if telemetry_index is not None:
                return SequenceTransition(consumed=True, dispatch=self._dispatch(state, telemetry_index))
            return SequenceTransition(consumed=True, terminal=self._finish(state, telemetry_status="not_started"))

    def mark_publish_failed(self, dispatch: RobotDispatch) -> SequenceTransition:
        with self._lock:
            state = self._by_topic.get(dispatch.topic)
            if state is None or state.current_action_id != dispatch.command_action_id:
                return SequenceTransition()
            command = state.commands[state.current_index]
            self._topic_by_action.pop(dispatch.command_action_id, None)
            state.events.append(
                {
                    "step_index": state.current_index + 1,
                    "role": command.role,
                    "action_id": dispatch.command_action_id,
                    "action": command.action,
                    "status": "publish_failed",
                }
            )
            if command.role == "telemetry":
                return SequenceTransition(
                    consumed=True,
                    terminal=self._finish(
                        state,
                        telemetry_status="publish_failed",
                        warning="telemetry_publish_failed",
                    ),
                )
            state.failure_error = "robot_command_publish_failed"
            state.failed_step = state.current_index + 1
            state.failed_action = command.action
            return SequenceTransition(consumed=True, terminal=self._finish(state, telemetry_status="not_started"))

    def expire(self, *, now: float | None = None) -> list[SequenceTransition]:
        timestamp = self._clock() if now is None else float(now)
        results: list[SequenceTransition] = []
        with self._lock:
            for state in list(self._by_topic.values()):
                if state.deadline > timestamp:
                    continue
                command = state.commands[state.current_index]
                self._topic_by_action.pop(state.current_action_id, None)
                state.events.append(
                    {
                        "step_index": state.current_index + 1,
                        "role": command.role,
                        "action_id": state.current_action_id,
                        "action": command.action,
                        "status": "timed_out",
                    }
                )
                if command.role == "telemetry":
                    terminal = self._finish(state, telemetry_status="timed_out", warning="telemetry_timeout")
                else:
                    state.failure_error = "robot_command_timeout"
                    state.failed_step = state.current_index + 1
                    state.failed_action = command.action
                    terminal = self._finish(state, telemetry_status="not_started")
                results.append(SequenceTransition(consumed=True, terminal=terminal))
        return results

    def _dispatch(self, state: _Sequence, index: int) -> RobotDispatch:
        command = state.commands[index]
        action_id = f"rg-{state.sequence_id}-{index + 1}"
        payload: dict[str, Any] = {"sender": "ai_server", "action_id": action_id, "action": command.action}
        payload.update(command.body)
        if command.action == "baseRotate":
            payload["speed"] = "veryslow"
        state.current_index = index
        state.current_action_id = action_id
        state.deadline = self._clock() + self.timeout_seconds
        self._topic_by_action[action_id] = state.topic
        return RobotDispatch(
            sequence_id=state.sequence_id,
            operation_id=state.operation_id,
            topic=state.topic,
            step_index=index + 1,
            role=command.role,
            command_action_id=action_id,
            payload=payload,
        )

    @staticmethod
    def _telemetry_index(state: _Sequence) -> int | None:
        for index, command in enumerate(state.commands):
            if command.role == "telemetry":
                return index
        return None

    def _finish(self, state: _Sequence, *, telemetry_status: str, warning: str | None = None) -> SequenceTerminal:
        self._topic_by_action.pop(state.current_action_id, None)
        self._by_topic.pop(state.topic, None)
        return SequenceTerminal(
            sequence_id=state.sequence_id,
            operation_id=state.operation_id,
            topic=state.topic,
            success=state.failure_error is None,
            error=state.failure_error,
            warning=warning,
            failed_step=state.failed_step,
            failed_action=state.failed_action,
            motion_steps_completed=state.motion_steps_completed,
            telemetry_status=telemetry_status,
            events=tuple(dict(event) for event in state.events),
        )


def validate_reach_and_grab_commands(commands: Sequence[RobotCommand]) -> tuple[RobotCommand, ...]:
    values = tuple(commands)
    actions = [command.action for command in values]
    allowed = (
        ["controlik", "gripper", "calibrationvalues"],
        ["baseRotate", "controlik", "gripper", "calibrationvalues"],
    )
    if actions not in allowed:
        raise ValueError(f"invalid reach-and-grab command sequence: {actions}")
    for command in values:
        if command.action == "baseRotate":
            if str(command.body.get("controlType") or "").upper() != "DEGREES":
                raise ValueError("baseRotate must use DEGREES")
            if str(command.body.get("direction") or "").upper() not in {"LEFT", "RIGHT"}:
                raise ValueError("baseRotate direction must be LEFT or RIGHT")
            if float(command.body.get("value", -1)) < 0:
                raise ValueError("baseRotate value must be non-negative")
        elif command.action == "controlik":
            float(command.body["distance"])
            float(command.body.get("z_height", 0.0))
        elif command.action == "gripper" and str(command.body.get("command") or "").upper() != "GRAB":
            raise ValueError("automatic gripper command must be GRAB")
        elif command.action == "calibrationvalues" and command.role != "telemetry":
            raise ValueError("calibrationvalues must be telemetry")
    return values

