from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Any, Callable, Dict, Optional

try:
    from ..mqtt_robot import ReachAndGrabResult, reach_and_grab_payload
    from ..models.reach_and_grab_state import ReachAndGrabState, explain_reach_and_grab_failure
    from ..views.reach_and_grab_view import ReachAndGrabView
except ImportError:  # pragma: no cover - direct script execution
    from mqtt_robot import ReachAndGrabResult, reach_and_grab_payload
    from models.reach_and_grab_state import ReachAndGrabState, explain_reach_and_grab_failure
    from views.reach_and_grab_view import ReachAndGrabView

try:
    from PIL import Image, ImageTk
except ImportError:  # Photo preview degrades to a text notice; the run still works.
    Image = None
    ImageTk = None


class ReachAndGrabController:
    """Wires the Reach and Grab view to the robot connection.

    Takes its collaborators as constructor arguments instead of reaching up
    into the main window, so this controller can be exercised without a live
    CalibrationWizard.
    """

    def __init__(
        self,
        view: ReachAndGrabView,
        *,
        sender: Callable[[], str],
        broker_connected: Callable[[], bool],
        run_reach_and_grab: Callable[[Dict[str, Any]], ReachAndGrabResult],
        run_worker: Callable[[str, Callable[[], Any], Optional[Callable[[Any], None]]], None],
        set_app_status: Callable[[str], None],
        set_last_result: Callable[[str], None],
        record_session_result: Callable[[str, Dict[str, Any]], None],
        record_capture_path: Callable[[Path], None],
    ) -> None:
        self.view = view
        self._sender = sender
        self._broker_connected = broker_connected
        self._run_reach_and_grab = run_reach_and_grab
        self._run_worker = run_worker
        self._set_app_status = set_app_status
        self._set_last_result = set_last_result
        self._record_session_result = record_session_result
        self._record_capture_path = record_capture_path
        self.state = ReachAndGrabState()

        view.on_run = self.run
        view.set_result_text("No request yet. Connect to MQTT, describe an object, and start reach-and-grab.")

    def run(self) -> None:
        if self.state.running:
            messagebox.showinfo("Reach and Grab", "A reach-and-grab request from this GUI is already in progress.")
            return
        if not self._broker_connected():
            messagebox.showerror("Reach and Grab", "Connect to MQTT before starting.")
            return

        target = self.view.target.get().strip()
        if not target:
            messagebox.showerror("Reach and Grab", "Enter a nonempty object description.")
            return

        try:
            box_threshold = float(self.view.box_threshold.get())
            text_threshold = float(self.view.text_threshold.get())
            magnet_position = int(self.view.magnet_position.get())
        except (TypeError, ValueError, tk.TclError):
            messagebox.showerror("Reach and Grab", "Thresholds must be numbers and magnet position must be a whole number.")
            return
        if not (0.0 <= box_threshold <= 1.0 and 0.0 <= text_threshold <= 1.0):
            messagebox.showerror("Reach and Grab", "Thresholds must be between 0 and 1.")
            return
        if magnet_position < 0:
            messagebox.showerror("Reach and Grab", "Magnet position must be zero or greater.")
            return

        workflow_text = self.view.workflow_id.get().strip()
        event_text = self.view.workflow_event_id.get().strip()
        try:
            workflow_id = int(workflow_text) if workflow_text else None
            workflow_event_id = int(event_text) if event_text else None
        except ValueError:
            messagebox.showerror("Reach and Grab", "Workflow IDs must be whole numbers.")
            return
        if workflow_event_id is not None and workflow_id is None:
            messagebox.showerror("Reach and Grab", "Workflow event ID requires a workflow ID.")
            return

        try:
            payload = reach_and_grab_payload(
                sender=self._sender(),
                phrase=target,
                use_model=bool(self.view.use_model.get()),
                model_name=self.view.model_name.get(),
                box_threshold=box_threshold,
                text_threshold=text_threshold,
                magnet_position=magnet_position,
                workflow_id=workflow_id,
                workflow_event_id=workflow_event_id,
            )
        except (TypeError, ValueError) as exc:
            messagebox.showerror("Reach and Grab", str(exc))
            return

        self.state.start(payload)
        self.view.action_text.set(f"Action ID: {self.state.current_action_id}")
        self.view.status_text.set("Requesting a fresh photo; waiting for firmware and the Vision server…")
        self.view.set_running(True)
        self.view.set_result_text(
            "Reach-and-grab started.\n\n"
            + json.dumps(payload, indent=2, sort_keys=True)
            + "\n\nThe GUI will not publish any child motion commands."
        )
        self._run_worker("reach and grab", lambda: self._run_reach_and_grab(payload), self._render_result)

    def handle_worker_error(self, error: str) -> None:
        """Called by the shared worker-error path when label == 'reach and grab'."""
        self.state.finish()
        self.view.set_running(False)
        if "Do not automatically resend" in error:
            self.view.status_text.set(
                "Timed out with an uncertain robot state. Do not automatically resend; "
                "the robot may already have moved. Late matching Vision results will still be shown."
            )
        else:
            self.view.status_text.set(f"Failed — {error}")
        self.view.set_result_text(
            f"Reach-and-grab did not produce a terminal result in this wait.\n\n{error}\n\n"
            "No retry was sent. Check the shared-topic activity log before deciding what to do next."
        )

    def display_photo_saved(self, path: Path) -> None:
        path = Path(path)
        self._record_capture_path(path)
        self.state.record_photo(path)
        if self.state.running:
            self.view.status_text.set("Detection photo received; waiting for Vision inference and robot execution…")
        if Image is None or ImageTk is None:
            self.view.show_photo_unavailable(
                f"Photo saved: {path}\nInstall Pillow to preview images:\npython3 -m pip install Pillow"
            )
            return
        image = Image.open(path)
        image.thumbnail((460, 300))
        self.view.show_photo(ImageTk.PhotoImage(image))

    def handle_progress(self, message: Dict[str, Any]) -> None:
        action_id = str(message.get("action_id") or "")
        if not self.state.is_current_action(action_id):
            return
        self.state.append_progress(message)

        sender = str(message.get("sender") or "")
        status = str(message.get("status") or "").lower()
        stage = str(message.get("stage") or "")
        log = str(message.get("log") or "")
        if sender == "firmware":
            if log == "sent":
                self.view.status_text.set("Firmware sent the photo; waiting for Vision inference and motion planning…")
            else:
                self.view.status_text.set("Firmware is capturing the detection photo…")
        elif status == "in_progress" and stage == "executing_reach_and_grab":
            step_count = message.get("motion_step_count")
            self.view.status_text.set(
                "Vision server is executing reach-and-grab"
                + (f" ({step_count} planned robot steps)…" if step_count is not None else "…")
            )
        elif status == "in_progress":
            self.view.status_text.set(str(message.get("log") or "Vision server is processing the detection…"))

        if sender == "visual_ai" and status in {"completed", "failed"}:
            self._render_terminal(message)
        else:
            self._render_progress_text()

    def _render_progress_text(self) -> None:
        lines = [
            f"Action ID: {self.state.current_action_id or '-'}",
            f"Target: {self.state.request.get('phrase', '-')}",
            "",
            "Progress:",
        ]
        if self.state.progress:
            for index, message in enumerate(self.state.progress, start=1):
                lines.append(f"{index}. {self.state.message_summary(message)}")
            lines.extend(["", "Latest message:", json.dumps(self.state.progress[-1], indent=2, sort_keys=True)])
        else:
            lines.append("Waiting for the first matching MQTT message…")
        self.view.set_result_text("\n".join(lines))

    def _render_terminal(self, response: Dict[str, Any], photo_path: Optional[Path] = None) -> None:
        status = str(response.get("status") or "unknown").lower()
        stage = str(response.get("stage") or "")
        grab_status = response.get("grab_status")
        physical_success = status == "completed" and stage == "reach_and_grab_completed" and grab_status == "completed"

        failure_explanation: list = []
        if physical_success:
            summary = "Completed — the Vision server confirmed the object was grabbed."
        elif status == "completed" and stage == "detection_only":
            summary = "Detection completed, but automatic robot movement is disabled."
        elif status == "failed":
            summary, failure_explanation = explain_reach_and_grab_failure(response)
        else:
            summary = "Completed response received, but a successful physical grab was not confirmed."

        execution_message = self.state.find_execution_message()
        raw_x = response.get("raw_x", execution_message.get("raw_x", "-"))
        raw_y = response.get("raw_y", execution_message.get("raw_y", "-"))

        self.state.finish()
        self.view.set_running(False)
        self.view.status_text.set(summary)
        self._set_app_status("Done: reach and grab" if physical_success else "Reach and grab finished")

        details = [
            summary,
            "",
            f"Action ID: {response.get('action_id', '-')}",
            f"Target: {response.get('phrase', self.state.request.get('phrase', '-'))}",
            f"Stage: {stage or '-'}",
            f"Vision image ID: {response.get('image_id', '-')}",
            f"Detection location: x={raw_x}% left-to-right, y={raw_y}% bottom-to-top",
            f"Motion steps completed: {response.get('motion_steps_completed', '-')}",
            f"Grab status: {grab_status if grab_status is not None else '-'}",
            f"Telemetry status: {response.get('telemetry_status', '-')}",
        ]
        if execution_message:
            rotation_control = execution_message.get("commanded_rotation_control_type")
            if rotation_control:
                details.append(
                    "Planned rotation: "
                    f"{rotation_control} "
                    f"{execution_message.get('commanded_rotation_direction', '')} "
                    f"{execution_message.get('commanded_rotation_value', '')}"
                )
            else:
                details.append("Planned rotation: none")
            details.append(
                "Planned IK: "
                f"distance={execution_message.get('commanded_ik_distance_mm', '-')} mm, "
                f"z={execution_message.get('commanded_ik_z_height_mm', '-')} mm"
            )
            details.append(f"Planned robot steps: {execution_message.get('motion_step_count', '-')}")
        if response.get("warning"):
            details.append(f"Warning: {response['warning']}")
        if response.get("error"):
            details.append(f"Error: {response['error']}")
        details.extend(failure_explanation)
        if response.get("failed_step") is not None:
            details.append(f"Failed step: {response['failed_step']}")
        if response.get("failed_action"):
            details.append(f"Failed action: {response['failed_action']}")
        if photo_path or self.state.photo_path:
            details.append(f"Photo: {photo_path or self.state.photo_path}")
        details.extend(["", "Progress:"])
        for index, message in enumerate(self.state.progress, start=1):
            details.append(f"{index}. {self.state.message_summary(message)}")
        details.extend(["", "Terminal Visual AI message:", json.dumps(response, indent=2, sort_keys=True)])
        self.view.set_result_text("\n".join(details))

        self._record_session_result(
            "reach_and_grab",
            {
                "request": self.state.request,
                "response": response,
                "photo": str(photo_path or self.state.photo_path or ""),
                "progress": self.state.progress,
            },
        )
        self._set_last_result("Reach-and-grab completed" if physical_success else "Reach-and-grab finished")

    def _render_result(self, result: ReachAndGrabResult) -> None:
        self.state.request = dict(result.request)
        self.state.progress = list(result.progress)
        if result.photo_path:
            self.state.photo_path = result.photo_path
        self._render_terminal(result.response, result.photo_path)
