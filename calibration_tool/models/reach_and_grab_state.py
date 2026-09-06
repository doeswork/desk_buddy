from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


def explain_reach_and_grab_failure(response: Dict[str, Any]) -> tuple[str, list[str]]:
    """Turn a Visual AI failure code into operator-facing timeout semantics."""
    error = str(response.get("error") or "unknown Vision server error")
    failed_action = str(response.get("failed_action") or "robot command")
    failed_step = response.get("failed_step")
    step_text = f"step {failed_step} ({failed_action})" if failed_step is not None else failed_action

    if error == "robot_command_timeout":
        return (
            f"Failed — firmware response timeout during {step_text}.",
            [
                "Timeout type: Vision-server per-command firmware response timeout",
                (
                    "Meaning: the Vision server published this robot command but did not receive "
                    "the exact matching firmware status=completed before its command deadline."
                ),
                (
                    "This was not a camera, detection, or GUI timeout. The command may have been "
                    "missed, may still have executed, or its response may have been lost. Treat the "
                    "robot's physical state as uncertain and do not automatically retry."
                ),
            ],
        )

    return f"Failed — {error}", []


class ReachAndGrabState:
    """In-flight and last-known state for one reach-and-grab run.

    A single instance is reused across runs; start() resets it, and progress
    messages / the terminal result mutate it in place, mirroring how a single
    action_id owns one run at a time in the wire protocol.
    """

    def __init__(self) -> None:
        self.running = False
        self.current_action_id = ""
        self.request: Dict[str, Any] = {}
        self.progress: List[Dict[str, Any]] = []
        self.photo_path: Optional[Path] = None

    def start(self, payload: Dict[str, Any]) -> None:
        self.running = True
        self.current_action_id = str(payload["action_id"])
        self.request = dict(payload)
        self.progress = []
        self.photo_path = None

    def record_photo(self, path: Path) -> None:
        self.photo_path = path

    def is_current_action(self, action_id: str) -> bool:
        return bool(action_id) and action_id == self.current_action_id

    def append_progress(self, message: Dict[str, Any]) -> None:
        self.progress.append(message)

    def finish(self) -> None:
        self.running = False

    @staticmethod
    def message_summary(message: Dict[str, Any]) -> str:
        sender = str(message.get("sender") or "unknown")
        status = str(message.get("status") or "message")
        stage = str(message.get("stage") or "")
        log = str(message.get("log") or "")
        error = str(message.get("error") or "")
        detail = stage or log or error
        if sender == "firmware" and log == "sent":
            detail = "photo sent"
        return f"{sender} · {status}" + (f" · {detail}" if detail else "")

    def find_execution_message(self) -> Dict[str, Any]:
        return next(
            (
                message
                for message in reversed(self.progress)
                if message.get("sender") == "visual_ai" and message.get("stage") == "executing_reach_and_grab"
            ),
            {},
        )
