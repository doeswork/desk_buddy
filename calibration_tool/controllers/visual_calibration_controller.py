from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Any, Callable, Dict, Optional

try:
    from ..mqtt_robot import VisualCalibrationCapture
    from ..models.visual_calibration_state import VisualCalibrationState
    from ..views.visual_calibration_view import VisualCalibrationView
except ImportError:  # pragma: no cover - direct script execution
    from mqtt_robot import VisualCalibrationCapture
    from models.visual_calibration_state import VisualCalibrationState
    from views.visual_calibration_view import VisualCalibrationView

try:
    from PIL import Image, ImageTk
except ImportError:  # Photo preview degrades to a text notice; capture still works.
    Image = None
    ImageTk = None


class VisualCalibrationController:
    """Wires the Visual Calibration view to the robot connection.

    Takes its collaborators as constructor arguments instead of reaching up
    into the main window: the capture function already binds capture_dir, so
    this controller does not need to know where photos are written.
    """

    def __init__(
        self,
        view: VisualCalibrationView,
        *,
        capture: Callable[..., VisualCalibrationCapture],
        run_worker: Callable[[str, Callable[[], Any], Optional[Callable[[Any], None]]], None],
        set_app_status: Callable[[str], None],
        set_last_result: Callable[[str], None],
        record_session_result: Callable[[str, Dict[str, Any]], None],
        record_capture_path: Callable[[Path], None],
    ) -> None:
        self.view = view
        self._capture = capture
        self._run_worker = run_worker
        self._set_app_status = set_app_status
        self._set_last_result = set_last_result
        self._record_session_result = record_session_result
        self._record_capture_path = record_capture_path
        self.state = VisualCalibrationState()

        view.on_capture = self.run_capture
        view.set_result_text("No result yet. Connect to MQTT and capture a visual calibration image.")

    def run_capture(self) -> None:
        try:
            magnet_position = int(self.view.magnet_position.get())
        except (TypeError, ValueError, tk.TclError):
            messagebox.showerror("Visual Calibration", "Magnet position must be a whole number.")
            return
        if magnet_position < 0:
            messagebox.showerror("Visual Calibration", "Magnet position must be zero or greater.")
            return

        self.view.status_text.set("Capturing a fresh camera image and waiting for the Vision server…")
        self.view.set_result_text("Waiting for firmware photo and Visual AI calibration result…")
        self._run_worker(
            "visual calibration",
            lambda: self._capture(magnet_position=magnet_position),
            self._render_result,
        )

    def display_photo_saved(self, path: Path) -> None:
        """Called when the firmware photo lands, before the Vision result."""
        path = Path(path)
        self._record_capture_path(path)
        self.view.status_text.set("Photo received; waiting for the Vision server to build the calibration grid…")
        if Image is None or ImageTk is None:
            self.view.show_photo_unavailable(
                f"Photo saved: {path}\nInstall Pillow to preview images:\npython3 -m pip install Pillow"
            )
            return
        image = Image.open(path)
        image.thumbnail((460, 300))
        self.view.show_photo(ImageTk.PhotoImage(image))

    def _render_result(self, capture: VisualCalibrationCapture) -> None:
        response = capture.response
        status = str(response.get("status") or "unknown").lower()
        points = response.get("calibration_points")
        point_count = len(points) if isinstance(points, dict) else 0
        image_id = response.get("image_id")

        lines = [
            f"Status: {status}",
            f"Action ID: {response.get('action_id', '-')}",
            f"Vision image ID: {image_id if image_id is not None else '-'}",
            f"Magnet position: {response.get('MagnetPosition', '-')}",
            f"Photo: {capture.photo_path}",
        ]

        if status == "completed":
            self._set_app_status("Done: visual calibration")
            self.view.status_text.set(
                f"Completed — {point_count} calibration points saved"
                + (f" as Vision image {image_id}" if image_id is not None else "")
                + "."
            )
            lines.extend(["", f"Calibration points ({point_count}):"])
            if isinstance(points, dict) and points:
                for name, values in points.items():
                    lines.append(f"{name}: {json.dumps(values, sort_keys=True, separators=(',', ':'))}")
            else:
                lines.append("The server completed without returning point details.")
        else:
            error = str(response.get("error") or "Visual AI calibration failed")
            self._set_app_status("Error: visual calibration")
            self.view.status_text.set(f"Failed — {error}")
            lines.extend(["", f"Error: {error}"])

        self.view.set_result_text("\n".join(lines))
        self.state.update(response, capture.photo_path)
        self._record_session_result("visual_calibration", {"photo": str(capture.photo_path), "response": response})
        self._set_last_result("Visual calibration completed" if status == "completed" else "Visual calibration failed")
