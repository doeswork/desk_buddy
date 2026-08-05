from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from tkinter import messagebox

try:
    from ..models.base_perch_state import perch_effective_signature
    from ..views.base_perch_view import BasePerchView
except ImportError:  # pragma: no cover - direct script execution
    from models.base_perch_state import perch_effective_signature
    from views.base_perch_view import BasePerchView


class BasePerchController:
    """Wires the Base + Perch view to the robot connection.

    Takes its collaborators as constructor arguments instead of reaching up
    into the main window: the payload functions are passed in as closures
    already bound to a sender, so this controller does not need to know the
    wire format.
    """

    def __init__(
        self,
        view: BasePerchView,
        *,
        robot_request: Callable[[Dict[str, Any]], Dict[str, Any]],
        run_worker: Callable[[str, Callable[[], Any], Optional[Callable[[Any], None]]], None],
        set_last_result: Callable[[str], None],
        record_session_result: Callable[[str, Dict[str, Any]], None],
        is_user_editing: Callable[[], bool],
        set_var_if_present: Callable[..., None],
        controller_angles: Dict[str, Any],
        base_profile_payload: Callable[[int], Dict[str, Any]],
        base_status_payload: Callable[[], Dict[str, Any]],
        perch_payload: Callable[[], Dict[str, Any]],
        save_perch_payload: Callable[[str, Any], Dict[str, Any]],
    ) -> None:
        self.view = view
        self._robot_request = robot_request
        self._run_worker = run_worker
        self._set_last_result = set_last_result
        self._record_session_result = record_session_result
        self._is_user_editing = is_user_editing
        self._set_var_if_present = set_var_if_present
        self._controller_angles = controller_angles
        self._base_profile_payload = base_profile_payload
        self._base_status_payload = base_status_payload
        self._perch_payload = perch_payload
        self._save_perch_payload = save_perch_payload
        self._last_sync_signature = ""

        view.on_run_profile_calibration = self.run_profile_calibration
        view.on_read_base_state = self.read_base_state
        view.on_copy_controller_targets = self.copy_controller_targets
        view.on_save_perch_pose = self.save_perch_pose
        view.on_move_to_saved_perch = self.move_to_saved_perch
        view.on_save_reach_landmarks = self.save_reach_landmarks
        view.on_capture_perch_photo = None  # assigned by app.py: shares the persistent capture_photo("perch")

    def run_profile_calibration(self) -> None:
        if not messagebox.askyesno(
            "Base Profile Calibration",
            "Base profile calibration may rotate the base multiple full turns. Continue?",
        ):
            return
        neutral = self.view.base_neutral.get()

        def work() -> Dict[str, Any]:
            response = self._robot_request(self._base_profile_payload(neutral))
            if str(response.get("status") or "").lower() != "completed":
                base = response.get("base_rotation")
                firmware_error = base.get("error") if isinstance(base, dict) else None
                raise RuntimeError(
                    f"Base profile calibration failed: {firmware_error or response.get('error') or 'firmware returned failed'}"
                )
            return response

        self._run_worker("base profile calibration", work, self._show_profile_result)

    def _show_profile_result(self, response: Dict[str, Any]) -> None:
        # Calibration telemetry is flat on base_rotation; there is no longer a
        # separate veryslow measurement phase, so verySlowValidated just mirrors
        # whether usable counts exist.
        base = response.get("base_rotation")
        if not isinstance(base, dict):
            self._set_last_result("Base profile completed, but the firmware did not return base rotation telemetry.")
            messagebox.showinfo(
                "Calibration Wizard", "Base profile completed, but the firmware did not return base rotation telemetry."
            )
            return

        validated = bool(base.get("verySlowValidated"))
        headline = (
            "Base profile and automatic veryslow verification passed."
            if validated
            else "Base profile completed, but veryslow verification did not pass."
        )
        summary = (
            f"{headline}\n\n"
            f"Veryslow learning passes: {base.get('calibrationPasses', '-')}\n"
            f"Full revolution left: {base.get('leftFullRevMs', '-')} ms, "
            f"{base.get('leftCountsPerRev', '-')} counts\n"
            f"Full revolution right: {base.get('rightFullRevMs', '-')} ms, "
            f"{base.get('rightCountsPerRev', '-')} counts"
        )
        self._set_last_result(
            "Base profile and veryslow verification passed" if validated else "Base profile completed, veryslow verification failed"
        )
        messagebox.showinfo("Base Profile Result", summary)

    def read_base_state(self) -> None:
        self._run_worker("base status", lambda: self._robot_request(self._base_status_payload()))

    def move_to_saved_perch(self) -> None:
        self._run_worker("move saved perch", lambda: self._robot_request(self._perch_payload()))

    def save_perch_pose(self) -> None:
        def work() -> None:
            self._robot_request(self._save_perch_payload("elbow", self.view.perch_angle_vars["ELBOW"].get()))
            self._robot_request(self._save_perch_payload("wrist", self.view.perch_angle_vars["WRIST"].get()))
            self._robot_request(self._save_perch_payload("twist", self.view.perch_angle_vars["TWIST"].get()))

        self._record_session_result(
            "perch", {name: var.get() for name, var in self.view.perch_angle_vars.items()}
        )
        self._run_worker("save perch values", work, lambda _: self._set_last_result("Perch pose saved"))

    def save_reach_landmarks(self) -> None:
        def work() -> None:
            for kind, var in self.view.perch_dist_vars.items():
                self._robot_request(self._save_perch_payload(kind, var.get()))

        self._record_session_result(
            "perch_distances", {kind: var.get() for kind, var in self.view.perch_dist_vars.items()}
        )
        self._run_worker("save perch distances", work, lambda _: self._set_last_result("Reach landmarks saved"))

    def copy_controller_targets(self) -> None:
        for name in ("ELBOW", "WRIST", "TWIST"):
            self.view.perch_angle_vars[name].set(self._controller_angles[name].get())

    def sync_from_calibrationvalues(self, values: Dict[str, Any]) -> None:
        """Called by the app's shared MQTT state-render pass on every update.

        Skips work when the effective perch pose hasn't changed since the
        last sync, or while the operator is mid-edit -- a heartbeat arriving
        during a pose edit must never overwrite what the operator just typed.
        """
        if not values or self._is_user_editing():
            return
        effective = values.get("perch_effective")
        if not isinstance(effective, dict):
            return
        signature = perch_effective_signature(effective)
        if signature == self._last_sync_signature:
            return
        for name in ("ELBOW", "WRIST", "TWIST"):
            self._set_var_if_present(self.view.perch_angle_vars[name], effective.get(name))
        for kind, key in (("min", "MIN"), ("mid", "MID"), ("max", "MAX")):
            self._set_var_if_present(self.view.perch_dist_vars[kind], effective.get(key))
        self._last_sync_signature = signature
