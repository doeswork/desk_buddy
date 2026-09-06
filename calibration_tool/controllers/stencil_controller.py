from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Any, Callable, Dict, Optional

try:
    from ..mqtt_robot import stencil_payload
    from ..models.stencil_state import StencilState, format_stencil_number, format_stencil_value
    from ..views.stencil_view import StencilView
except ImportError:  # pragma: no cover - direct script execution
    from mqtt_robot import stencil_payload
    from models.stencil_state import StencilState, format_stencil_number, format_stencil_value
    from views.stencil_view import StencilView


class StencilController:
    """Wires the Stencil view to the robot connection.

    Takes its collaborators (robot request, worker runner, status refresh,
    sender id, bool formatter) as constructor arguments instead of reaching
    up into the main window, so this controller can be exercised without a
    live CalibrationWizard.
    """

    FIELDS = [
        ("Phase", "phase"),
        ("Active", "active"),
        ("Session", "sessionId"),
        ("Point index", "pointIndex"),
        ("Total points", "totalPointCount"),
        ("Offset points", "offsetPointCount"),
        ("Validation points", "validationPointCount"),
        ("Home direction", "homeDirection"),
        ("Base move speed", "baseMoveSpeed"),
        ("Base move skipped", "baseMoveSkipped"),
        ("Last base target", "lastBaseTargetAngleDegrees"),
        ("Point id", "pointId"),
        ("Base angle", "baseAngleDegrees"),
        ("Base distance", "baseDistanceMm"),
        ("Z height", "zHeightMm"),
        ("Offset contributor", "offsetContributor"),
        ("Target angle", "targetAngleDegrees"),
        ("Target distance", "targetDistanceMm"),
        ("Target z height", "targetZHeightMm"),
        ("Rotation nudge", "rotationNudgeDegrees"),
        ("Distance nudge", "distanceNudgeMm"),
        ("Attempts", "attempts"),
        ("Grabbed", "grabbed"),
        ("Saved rotation offset", "savedRotationOffsetDegrees"),
        ("Saved IK offset", "savedIkOffsetMm"),
        ("Message", "message"),
        ("Error", "error"),
    ]

    def __init__(
        self,
        view: StencilView,
        *,
        sender: Callable[[], str],
        robot_request: Callable[[Dict[str, Any]], Dict[str, Any]],
        run_worker: Callable[[str, Callable[[], Any], Optional[Callable[[Any], None]]], None],
        refresh_status: Callable[[], None],
        format_bool: Callable[[Any], str],
    ) -> None:
        self.view = view
        self._sender = sender
        self._robot_request = robot_request
        self._run_worker = run_worker
        self._refresh_status = refresh_status
        self._format_bool = format_bool
        self.state = StencilState()

        view.on_start = self.start
        view.on_run_point = self.run_point
        view.on_status_request = self.status_request
        view.on_cancel = self.cancel
        view.on_clear = self.clear
        view.on_adjust = self.adjust
        view.on_adjust_previous_retry = self.adjust_previous_retry

        self.render({})

    def start(self) -> None:
        self._run_command("start stencil session", "START")

    def run_point(self) -> None:
        self._run_command("run stencil point", "RUN_POINT")

    def status_request(self) -> None:
        self._run_command("stencil status", "STATUS")

    def cancel(self) -> None:
        self._run_command("cancel stencil session", "CANCEL")

    def clear(self) -> None:
        if not messagebox.askyesno(
            "Clear Saved Stencil",
            "Clear saved stencil offsets from the robot? This removes st_map, rot_off_deg, and ik_off_mm.",
        ):
            return
        self._run_command("clear saved stencil", "CLEAR", refresh_after=True)

    def adjust(self) -> None:
        self._run_command(
            "adjust stencil point",
            "ADJUST",
            rotation=float(self.view.rotation_nudge.get()),
            distance=float(self.view.distance_nudge.get()),
        )

    def adjust_previous_retry(self) -> None:
        self._run_command(
            "adjust and retry previous stencil point",
            "ADJUST_PREVIOUS",
            rotation=float(self.view.rotation_nudge.get()),
            distance=float(self.view.distance_nudge.get()),
        )

    def _run_command(
        self,
        label: str,
        command: str,
        rotation: Optional[float] = None,
        distance: Optional[float] = None,
        refresh_after: bool = False,
    ) -> None:
        payload = stencil_payload(self._sender(), command, rotation=rotation, distance=distance)

        def after(response: Dict[str, Any]) -> None:
            self.render(response)
            stencil = response.get("stencil_calibration") if isinstance(response, dict) else None
            phase = stencil.get("phase") if isinstance(stencil, dict) else ""
            if refresh_after or phase in {"complete", "cleared"}:
                self._refresh_status()

        self._run_worker(label, lambda: self._robot_request(payload), after)

    def render(self, response: Dict[str, Any]) -> None:
        stencil = self.state.update_from_response(response)

        self.view.status_box.delete("1.0", tk.END)
        self.view.points_box.delete("1.0", tk.END)

        if not stencil:
            self.view.status_box.insert(tk.END, "No stencil status yet. Click Status or Start Session.")
            self.view.points_box.insert(tk.END, "No point progress yet.")
            return

        for label, key in self.FIELDS:
            value = stencil.get(key)
            if value is not None and value != "":
                self.view.status_box.insert(tk.END, f"{label}: {self._format_value(value)}\n")

        points = stencil.get("points")
        if not isinstance(points, list):
            self.view.points_box.insert(tk.END, "No point progress in response.")
            return

        self.view.points_box.insert(
            tk.END, "idx  point             z  angle  dist  offset  done  grab  rotNudge  distNudge  attempts\n"
        )
        self.view.points_box.insert(
            tk.END, "---  ----------------  --  -----  ----  ------  ----  ----  --------  ---------  --------\n"
        )
        for idx, point in enumerate(points):
            if not isinstance(point, dict):
                continue
            angle = point.get("angleDegrees", point.get("angle"))
            distance = point.get("distanceMm", point.get("distance"))
            z_height = point.get("zHeightMm", point.get("z"))
            self.view.points_box.insert(
                tk.END,
                (
                    f"{idx:>3}  "
                    f"{str(point.get('id', '-')):<16}  "
                    f"{format_stencil_number(z_height):>2}  "
                    f"{format_stencil_number(angle):>5}  "
                    f"{format_stencil_number(distance):>4}  "
                    f"{self._format_bool(point.get('offsetContributor')):<6}  "
                    f"{self._format_bool(point.get('completed')):<4}  "
                    f"{self._format_bool(point.get('grabbed')):<4}  "
                    f"{format_stencil_number(point.get('rotationNudgeDegrees')):>8}  "
                    f"{format_stencil_number(point.get('distanceNudgeMm')):>9}  "
                    f"{self._format_value(point.get('attempts')):>8}\n"
                ),
            )

    def _format_value(self, value: Any) -> str:
        return format_stencil_value(value, self._format_bool)
