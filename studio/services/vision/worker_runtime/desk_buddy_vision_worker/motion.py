"""Exact-acknowledgement, never-retrying firmware command sequencing."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Mapping


class MotionError(RuntimeError):
    def __init__(self, code: str, message: str, *, uncertain: bool = False, response=None) -> None:
        super().__init__(message)
        self.code = code
        self.uncertain = uncertain
        self.response = dict(response) if isinstance(response, Mapping) else None


@dataclass
class _Waiter:
    event_topic: str
    event: threading.Event
    response: dict[str, Any] | None = None
    disconnected: bool = False


class CommandSequencer:
    def __init__(self, publish, *, timeout_seconds: float = 30.0) -> None:
        self.publish = publish
        self.timeout_seconds = float(timeout_seconds)
        self._lock = threading.RLock()
        self._waiters: dict[str, _Waiter] = {}

    def execute(
        self, *, command_topic: str, event_topic: str, operation_id: str,
        index: int, action: str, fields: Mapping[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        action_id = f"rg-{operation_id[:10]}-{index}-{uuid.uuid4().hex[:6]}"
        waiter = _Waiter(event_topic, threading.Event())
        with self._lock:
            self._waiters[action_id] = waiter
        payload = {"sender": "ai_server", "action_id": action_id, "action": action}
        payload.update(dict(fields or {}))
        try:
            published = self.publish(command_topic, json.dumps(payload, separators=(",", ":")), qos=1)
            rc = getattr(published, "rc", 0)
            if rc not in (None, 0):
                raise MotionError("command_publish_failed", f"Could not publish {action}.")
            if not waiter.event.wait(self.timeout_seconds):
                raise MotionError(
                    "command_timeout", f"Timed out waiting for {action} acknowledgement.",
                    uncertain=True,
                )
            if waiter.disconnected:
                raise MotionError(
                    "connection_lost", f"MQTT disconnected while {action} may have been moving.",
                    uncertain=True,
                )
            response = waiter.response or {}
            if response.get("status") != "completed":
                error = response.get("error")
                message = (
                    str(error.get("message")) if isinstance(error, Mapping) and error.get("message")
                    else f"Firmware reported that {action} failed."
                )
                raise MotionError("robot_command_failed", message, uncertain=True, response=response)
            return action_id, response
        finally:
            with self._lock:
                self._waiters.pop(action_id, None)

    def handle_event(self, topic: str, body: Mapping[str, Any]) -> bool:
        if body.get("sender") != "firmware" or body.get("status") not in {"completed", "failed"}:
            return False
        action_id = str(body.get("action_id") or "")
        with self._lock:
            waiter = self._waiters.get(action_id)
            if waiter is None or waiter.event_topic != topic:
                return False
            waiter.response = dict(body)
            waiter.event.set()
        return True

    def disconnected(self) -> None:
        with self._lock:
            waiters = tuple(self._waiters.values())
            for waiter in waiters:
                waiter.disconnected = True
                waiter.event.set()
