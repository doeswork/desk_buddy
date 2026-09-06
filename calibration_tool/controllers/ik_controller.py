from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Any, Callable, Dict, Optional

try:
    from ..models.ik_state import FIRMWARE_KEY_BY_ROW, firmware_points_signature
    from ..views.ik_view import IkView
except ImportError:  # pragma: no cover - direct script execution
    from models.ik_state import FIRMWARE_KEY_BY_ROW, firmware_points_signature
    from views.ik_view import IkView


class IkController:
    """Wires the IK view to the robot connection and to the persistent
    controller panel it borrows angles from and moves through.

    Takes its collaborators as constructor arguments instead of reaching up
    into the main window: ik_payload/save_hover_payload are passed in as
    small closures already bound to a sender, so this controller does not
    need to know the wire format.
    """

    def __init__(
        self,
        view: IkView,
        *,
        robot_request: Callable[[Dict[str, Any]], Dict[str, Any]],
        run_worker: Callable[[str, Callable[[], Any], Optional[Callable[[Any], None]]], None],
        refresh_status: Callable[[], None],
        set_last_result: Callable[[str], None],
        record_session_result: Callable[[str, Dict[str, Any]], None],
        is_user_editing: Callable[[], bool],
        set_var_if_present: Callable[[tk.Variable, Any], None],
        controller_angles: Dict[str, tk.IntVar],
        move_all_servos: Callable[[], None],
        capture_photo: Callable[[str], None],
        ik_payload: Callable[[float, float], Dict[str, Any]],
        save_hover_payload: Callable[[str, str, float, int, int, int], Dict[str, Any]],
    ) -> None:
        self.view = view
        self._robot_request = robot_request
        self._run_worker = run_worker
        self._refresh_status = refresh_status
        self._set_last_result = set_last_result
        self._record_session_result = record_session_result
        self._is_user_editing = is_user_editing
        self._set_var_if_present = set_var_if_present
        self._controller_angles = controller_angles
        self._move_all_servos = move_all_servos
        self._capture_photo = capture_photo
        self._ik_payload = ik_payload
        self._save_hover_payload = save_hover_payload
        self._last_sync_signature = ""

        view.on_send_direct_ik = self.send_direct_ik
        view.on_move_controller_targets = move_all_servos
        view.on_capture_photo = lambda: capture_photo("ik")
        view.on_import = self.use_controller_for_row
        view.on_move = self.move_row
        view.on_save = self.save_row
        view.on_run_validation = self.run_validation
        view.on_mark_pass = lambda plane, kind: self.mark_validation(plane, kind, True)
        view.on_mark_fail = lambda plane, kind: self.mark_validation(plane, kind, False)

    @property
    def rows(self) -> Dict[str, Dict[str, Dict[str, tk.Variable]]]:
        return self.view.rows

    def use_controller_for_row(self, plane: str, kind: str) -> None:
        row = self.rows[plane][kind]
        row["elbow"].set(self._controller_angles["ELBOW"].get())
        row["wrist"].set(self._controller_angles["WRIST"].get())
        row["twist"].set(self._controller_angles["TWIST"].get())

    def send_direct_ik(self) -> None:
        try:
            distance_y = int(self.view.control_y.get())
            height_z = int(self.view.control_z.get())
        except (tk.TclError, TypeError, ValueError):
            messagebox.showerror("Calibration Wizard", "Enter whole-number Y and Z values.")
            return

        if distance_y < 0:
            messagebox.showerror("Calibration Wizard", "Y distance must be 0 mm or greater.")
            return
        if height_z < 0 or height_z > 50:
            messagebox.showerror("Calibration Wizard", "Z height must be between 0 and 50 mm.")
            return

        def work() -> Dict[str, Any]:
            response = self._robot_request(self._ik_payload(float(distance_y), float(height_z)))
            if str(response.get("status") or "").lower() != "completed":
                raise RuntimeError(
                    f"Robot rejected IK target Y={distance_y} mm, Z={height_z} mm. "
                    "Check that the requested point is inside the calibrated workspace."
                )
            return response

        self._run_worker(
            f"IK Y={distance_y} mm Z={height_z} mm",
            work,
            lambda _response: self._set_last_result(f"IK completed: Y={distance_y} mm, Z={height_z} mm"),
        )

    def move_row(self, plane: str, kind: str) -> None:
        row = self.rows[plane][kind]
        self._controller_angles["ELBOW"].set(int(row["elbow"].get()))
        self._controller_angles["WRIST"].set(int(row["wrist"].get()))
        self._controller_angles["TWIST"].set(int(row["twist"].get()))
        self._move_all_servos()

    def save_row(self, plane: str, kind: str) -> None:
        row = self.rows[plane][kind]
        payload = self._save_hover_payload(
            plane, kind, float(row["distance"].get()), int(row["elbow"].get()), int(row["wrist"].get()), int(row["twist"].get())
        )
        self._record_session_result(
            "ik_saved",
            {
                plane: {
                    kind: {
                        "distance": row["distance"].get(),
                        "ELBOW": row["elbow"].get(),
                        "WRIST": row["wrist"].get(),
                        "TWIST": row["twist"].get(),
                    }
                }
            },
        )
        self._run_worker(f"save {plane} {kind}", lambda: self._robot_request(payload), lambda _: self._after_saved(row))

    def _after_saved(self, row: Dict[str, tk.Variable]) -> None:
        row["result"].set("Saved")
        self._refresh_status()

    def run_validation(self, plane: str, kind: str) -> None:
        row = self.rows[plane][kind]
        distance = float(row["distance"].get())
        self._run_worker(
            f"run IK {kind} z=25",
            lambda: self._robot_request(self._ik_payload(distance, 25.0)),
            lambda _: row["result"].set("Ran IK z=25"),
        )

    def mark_validation(self, plane: str, kind: str, passed: bool) -> None:
        row = self.rows[plane][kind]
        row["result"].set("PASS" if passed else "FAIL")
        self._record_session_result(
            "ik_validation",
            {plane: {kind: {"passed": passed, "distance": row["distance"].get()}}},
        )

    def sync_from_calibrationvalues(self, values: Dict[str, Any]) -> None:
        """Called by the app's shared MQTT state-render pass on every update.

        Skips work when the saved-point values haven't changed since the
        last sync, or while the operator is mid-edit -- a heartbeat arriving
        during a row edit must never overwrite what the operator just typed.
        """
        if not values:
            return
        signature = firmware_points_signature(values)
        if signature == self._last_sync_signature:
            return
        if self._is_user_editing():
            return

        for (plane, kind), firmware_key in FIRMWARE_KEY_BY_ROW.items():
            point = values.get(firmware_key)
            if not isinstance(point, dict):
                continue
            row = self.rows.get(plane, {}).get(kind)
            if not row:
                continue
            self._set_var_if_present(row["distance"], point.get("DISTANCE"))
            self._set_var_if_present(row["elbow"], point.get("ELBOW"))
            self._set_var_if_present(row["wrist"], point.get("WRIST"))
            self._set_var_if_present(row["twist"], point.get("TWIST"))
            row["result"].set("Saved in firmware")

        self._last_sync_signature = signature
