from __future__ import annotations

import json
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk
import traceback
from typing import Any, Callable, Dict, Optional

try:
    from PIL import Image, ImageTk
except ImportError:  # The app still starts; photo display explains the fix.
    Image = None
    ImageTk = None

try:
    from .config import DEFAULT_CA_CERT, ENV_PATH, WizardConfig, load_config, save_config
    from .mqtt_robot import (
        MqttRobot,
        ReachAndGrabResult,
        VisualCalibrationCapture,
        base_angle_payload,
        base_degrees_payload,
        base_profile_payload,
        base_status_payload,
        base_steps_payload,
        gripper_payload,
        ik_payload,
        perch_payload,
        reach_and_grab_payload,
        save_hover_payload,
        save_perch_payload,
        servo_payload,
        stencil_payload,
    )
except ImportError:  # pragma: no cover - direct script execution
    from config import DEFAULT_CA_CERT, ENV_PATH, WizardConfig, load_config, save_config
    from mqtt_robot import (
        MqttRobot,
        ReachAndGrabResult,
        VisualCalibrationCapture,
        base_angle_payload,
        base_degrees_payload,
        base_profile_payload,
        base_status_payload,
        base_steps_payload,
        gripper_payload,
        ik_payload,
        perch_payload,
        reach_and_grab_payload,
        save_hover_payload,
        save_perch_payload,
        servo_payload,
        stencil_payload,
    )


CAPTURE_DIR = Path(__file__).resolve().parent / "captures"
SESSION_DIR = Path(__file__).resolve().parent / "sessions"
MAX_TOPIC_LOG_LINES = 500
MAX_TOPIC_LOG_PAYLOAD_CHARS = 900
SURFACE = "#f4f6f8"
PANEL = "#ffffff"
INK = "#17202a"
MUTED = "#637083"
ACCENT = "#176b87"
ACCENT_DARK = "#0f5369"
GOOD = "#1f7a4d"
WARN = "#9a6700"
BAD = "#b42318"
UI_ZOOM_LEVELS = (0.75, 0.85, 1.0, 1.1, 1.25, 1.4, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0)

# Preferred families first, then cross-platform fallbacks. Anything that is not
# installed is skipped: Tk silently falls back to the bitmap "fixed" font, which
# ignores size changes entirely and would break zooming.
UI_FONT_CANDIDATES = (
    "Segoe UI",
    "Adwaita Sans",
    "Cantarell",
    "Noto Sans",
    "DejaVu Sans",
    "Liberation Sans",
    "Ubuntu",
    "Helvetica Neue",
    "Arial",
)
MONO_FONT_CANDIDATES = (
    "Consolas",
    "Adwaita Mono",
    "CaskaydiaMono Nerd Font",
    "Noto Sans Mono",
    "DejaVu Sans Mono",
    "Liberation Mono",
    "Ubuntu Mono",
    "Menlo",
    "Courier New",
)


def fit_image_to_width(source_width: int, source_height: int, available_width: int) -> tuple[int, int]:
    """Return uncropped image dimensions that fit the available pane width."""
    if source_width <= 0 or source_height <= 0 or available_width <= 0:
        raise ValueError("Image and available widths must be positive")
    output_width = min(source_width, available_width)
    output_height = max(1, round(source_height * (output_width / source_width)))
    return output_width, output_height


def build_calibration_status_rows(values: Dict[str, Any]) -> list[Dict[str, str]]:
    """Turn the firmware calibration dump into human-readable preference rows."""
    rows: list[Dict[str, str]] = []

    def add(
        group: str,
        label: str,
        key: str,
        value: Any,
        state: str,
        source: str,
    ) -> None:
        rows.append(
            {
                "group": group,
                "label": label,
                "key": key,
                "value": _format_preference_value(key, value),
                "state": state,
                "source": source,
            }
        )

    # Veryslow angles are fixed offsets from neutral rather than learned values,
    # so there is no veryslow validation state left to report here.
    base_fields = (
        ("Profile calibrated", "base_rotation_profileCalibrated", "boolean"),
        ("Rotation calibrated", "base_rotation_calibrated", "boolean"),
        ("Left counts / revolution", "base_rotation_leftCountsPerRev", "positive"),
        ("Right counts / revolution", "base_rotation_rightCountsPerRev", "positive"),
        ("Last position trusted", "base_rotation_lastValid", "boolean"),
    )
    for label, key, check in base_fields:
        value = values.get(key)
        ready = bool(value) if check == "boolean" else _is_positive_number(value)
        add(
            "Base rotation",
            label,
            key,
            value,
            "SAVED" if ready else "MISSING",
            "Calibration result" if ready else "Run base profile",
        )

    perch_effective = values.get("perch_effective")
    if not isinstance(perch_effective, dict):
        perch_effective = {}
    perch_fields = (
        ("Elbow perch angle", "PERCH_ELBOW_ANGLE", "ELBOW", 120),
        ("Wrist perch angle", "PERCH_WRIST_ANGLE", "WRIST", 90),
        ("Twist perch angle", "PERCH_TWIST_ANGLE", "TWIST", 90),
        ("Minimum reach", "PERCH_MIN", "MIN", 0),
        ("Middle reach", "PERCH_MID", "MID", 50),
        ("Maximum reach", "PERCH_MAX", "MAX", 100),
    )
    for label, key, effective_key, fallback in perch_fields:
        saved_value = values.get(key)
        if saved_value is None:
            add(
                "Perch",
                label,
                key,
                perch_effective.get(effective_key, fallback),
                "DEFAULT",
                "Firmware default",
            )
        else:
            add("Perch", label, key, saved_value, "SAVED", "User saved")

    hover_fields = (
        ("Table plane · minimum", "hover_over_min", True),
        ("Table plane · middle", "hover_over_mid", True),
        ("Table plane · maximum", "hover_over_max", True),
        ("Upper plane · minimum", "hover_min_120", False),
        ("Upper plane · middle", "hover_mid_120", False),
        ("Upper plane · maximum", "hover_max_120", False),
    )
    for label, key, required in hover_fields:
        value = values.get(key)
        if isinstance(value, dict):
            add("IK points", label, key, value, "SAVED", "User saved")
        else:
            add(
                "IK points",
                label,
                key,
                value,
                "MISSING" if required else "OPTIONAL",
                "Required" if required else "Not saved",
            )

    stencil_fields = (
        ("Rotation correction", "rot_off_deg"),
        ("Reach correction", "ik_off_mm"),
        ("Point calibration map", "st_map"),
    )
    for label, key in stencil_fields:
        value = values.get(key)
        add(
            "Stencil",
            label,
            key,
            value,
            "SAVED" if value is not None else "MISSING",
            "User saved" if value is not None else "Run stencil calibration",
        )

    return rows


def _is_positive_number(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _format_preference_value(key: str, value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if key == "st_map":
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            points = parsed.get("points")
            if isinstance(points, list):
                return f"{len(points)} saved points"
        return "Saved"
    if isinstance(value, dict):
        distance = value.get("DISTANCE")
        elbow = value.get("ELBOW")
        wrist = value.get("WRIST")
        twist = value.get("TWIST")
        return f"{distance} mm  ·  E {elbow}°  W {wrist}°  T {twist}°"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def is_heartbeat_event(kind: str, payload: Any) -> bool:
    """True when an event is routine heartbeat traffic the operator may hide."""
    if kind != "message" or not isinstance(payload, dict):
        return False
    return payload.get("log") == "heartbeat" or payload.get("status") == "heartbeat"


def format_base_rotation_progress(payload: Dict[str, Any]) -> Optional[str]:
    """Render a firmware base-rotation progress snapshot as one readable line.

    These arrive every true-north hit and every few seconds in between, so a raw
    JSON dump would bury the fields that actually show whether the run advanced.
    """
    progress = payload.get("progress")
    if not isinstance(progress, dict):
        return None

    event = str(progress.get("event", "progress"))
    parts = [f"PROGRESS {event}"]

    phase = progress.get("measure_phase") or progress.get("phase")
    if phase:
        parts.append(str(phase))

    for label, key in (
        ("enc", "encoder_unwrapped"),
        ("hits", "true_north_hits"),
        ("ignored", "ignored_pulses"),
        ("pulse_ms", "pulse_ms"),
        ("pulse_counts", "pulse_counts"),
        ("min_counts", "pulse_min_counts"),
    ):
        value = progress.get(key)
        if value is not None:
            parts.append(f"{label}={value}")

    if progress.get("true_north_pressed"):
        parts.append("TN=pressed")
    if progress.get("drive_angle") is not None:
        parts.append(f"angle={progress['drive_angle']}")
    if progress.get("resting") is not None:
        parts.append(f"rest={progress['resting']}")

    return " ".join(parts)


def format_topic_log_event(kind: str, payload: Any) -> Optional[str]:
    """Return a compact topic-log line body, or None when it should be hidden."""
    if kind == "message" and isinstance(payload, dict):
        if payload.get("status") == "progress":
            line = format_base_rotation_progress(payload)
            if line:
                return f"IN {line}"
        status = payload.get("status") or payload.get("photo") or payload.get("debug") or payload.get("log") or "message"
        action_id = payload.get("action_id", "")
        return _compact_log_line("IN", str(status), str(action_id), payload)

    if kind == "raw_message" and isinstance(payload, dict):
        topic = str(payload.get("topic", ""))
        text = str(payload.get("text", ""))
        if len(text) > MAX_TOPIC_LOG_PAYLOAD_CHARS:
            text = text[: MAX_TOPIC_LOG_PAYLOAD_CHARS - 3] + "..."
        return " ".join(part for part in ("IN", "raw", topic, text) if part)

    if kind == "sent" and isinstance(payload, dict):
        action = payload.get("action", "command")
        action_id = payload.get("action_id", "")
        return _compact_log_line("OUT", str(action), str(action_id), payload)

    if kind == "photo":
        action_id = getattr(payload, "action_id", "")
        return f"PHOTO {action_id} received raw JPEG".strip()

    if kind == "photo_saved":
        return f"APP saved photo {payload}"

    if kind == "worker_success":
        label = payload[0] if isinstance(payload, tuple) and payload else str(payload)
        if label == "reach and grab":
            return "APP reach and grab terminal result received"
        return f"APP {label} completed"

    if kind == "worker_error":
        if isinstance(payload, tuple) and len(payload) >= 2:
            return f"ERROR {payload[0]} failed: {payload[1]}"
        return f"ERROR {payload}"

    if kind == "error":
        return f"ERROR {payload}"

    if kind == "status":
        return f"APP {payload}"

    return None


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


def _compact_log_line(direction: str, label: str, action_id: str, payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
    if len(encoded) > MAX_TOPIC_LOG_PAYLOAD_CHARS:
        encoded = encoded[: MAX_TOPIC_LOG_PAYLOAD_CHARS - 3] + "..."
    parts = [direction, label]
    if action_id:
        parts.append(action_id)
    parts.append(encoded)
    return " ".join(parts)


class CalibrationWizard(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Desk Buddy · Calibration")
        initial_width = max(900, min(1360, self.winfo_screenwidth() - 120))
        initial_height = max(650, min(860, self.winfo_screenheight() - 140))
        self.geometry(f"{initial_width}x{initial_height}")
        self.minsize(900, 650)
        self.resizable(True, True)
        self.configure(background=SURFACE)
        self.ui_zoom = 1.0
        self.zoom_text = tk.StringVar(value="100%")
        self.maximize_text = tk.StringVar(value="Maximize")
        self._restore_geometry = ""
        self._manual_maximized = False
        self._configure_styles()
        self.bind("<Control-minus>", lambda _event: self._zoom_out())
        self.bind("<Control-plus>", lambda _event: self._zoom_in())
        self.bind("<Control-equal>", lambda _event: self._zoom_in())
        self.bind("<Control-0>", lambda _event: self._reset_zoom())

        self.config_values = load_config()
        self.events: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self.robot = MqttRobot(self._queue_event)
        self.controller_angles = {"ELBOW": tk.IntVar(value=90), "WRIST": tk.IntVar(value=90), "TWIST": tk.IntVar(value=90)}
        self.observed_angles = {
            "ELBOW": tk.StringVar(value="-"),
            "WRIST": tk.StringVar(value="-"),
            "TWIST": tk.StringVar(value="-"),
            "GRIPPER": tk.StringVar(value="-"),
        }
        self.perch_angle_vars = {"ELBOW": tk.IntVar(value=90), "WRIST": tk.IntVar(value=90), "TWIST": tk.IntVar(value=90)}
        self.base_neutral = tk.IntVar(value=90)
        self.base_rotation_value = tk.IntVar(value=10)
        self.base_direction = tk.StringVar(value="LEFT")
        self.base_speed = tk.StringVar(value="slow")
        self.observed_base_text = tk.StringVar(value="Base: no status yet")
        self.ik_control_y = tk.IntVar(value=100)
        self.ik_control_z = tk.IntVar(value=0)
        self.stencil_rotation_nudge = tk.DoubleVar(value=0.0)
        self.stencil_distance_nudge = tk.DoubleVar(value=0.0)
        self.stencil_status: Dict[str, Any] = {}
        self.visual_calibration_magnet_position = tk.IntVar(value=1)
        self.visual_calibration_status_text = tk.StringVar(value="Not run yet")
        self.reach_and_grab_target = tk.StringVar(value="")
        self.reach_and_grab_use_model = tk.BooleanVar(value=False)
        self.reach_and_grab_model_name = tk.StringVar(value="")
        self.reach_and_grab_box_threshold = tk.DoubleVar(value=0.35)
        self.reach_and_grab_text_threshold = tk.DoubleVar(value=0.25)
        self.reach_and_grab_magnet_position = tk.IntVar(value=1)
        self.reach_and_grab_workflow_id = tk.StringVar(value="")
        self.reach_and_grab_workflow_event_id = tk.StringVar(value="")
        self.reach_and_grab_status_text = tk.StringVar(value="Not run yet")
        self.reach_and_grab_action_text = tk.StringVar(value="Action ID: —")
        self.reach_and_grab_running = False
        self.reach_and_grab_current_action_id = ""
        self.reach_and_grab_request: Dict[str, Any] = {}
        self.reach_and_grab_progress: list[Dict[str, Any]] = []
        self.live_mode = tk.BooleanVar(value=False)
        self.user_editing_until = 0.0
        self.last_photo_path: Optional[Path] = None
        self.photo_image: Any = None
        self.photo_labels: list[ttk.Label] = []
        self.controller_photo_image: Any = None
        self.controller_photo_resize_job: Optional[str] = None
        self.controller_photo_render_width = 0
        self.visual_calibration_photo_image: Any = None
        self.reach_and_grab_photo_image: Any = None
        self.reach_and_grab_photo_path: Optional[Path] = None
        # (line, is_heartbeat) so the heartbeat filter can re-render history
        # rather than only affecting messages that arrive after it is toggled.
        self.topic_log_entries: list[tuple[str, bool]] = []
        self.show_heartbeats = tk.BooleanVar(value=False)
        self._last_ik_sync_signature = ""
        self._last_perch_sync_signature = ""
        self.session: Dict[str, Any] = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "captures": [], "ik_validation": {}}

        self.status_text = tk.StringVar(value="Not connected")
        self.robot_text = tk.StringVar(value="Robot offline")
        self.heartbeat_text = tk.StringVar(value="No heartbeat yet")
        self.last_result_text = tk.StringVar(value="No command sent yet")
        self.status_summary_vars = {
            "overall": tk.StringVar(value="Waiting for robot"),
            "base": tk.StringVar(value="Not checked"),
            "perch": tk.StringVar(value="Not checked"),
            "ik": tk.StringVar(value="Not checked"),
            "stencil": tk.StringVar(value="Not checked"),
        }
        self.system_status_vars = {
            "firmware": tk.StringVar(value="—"),
            "heartbeat": tk.StringVar(value="—"),
            "reset": tk.StringVar(value="—"),
            "error": tk.StringVar(value="None"),
        }

        self._build_ui()
        self._load_settings_into_form()
        self.after(150, self._process_events)
        self.after(1000, self._tick_connection_status)

    def _queue_event(self, kind: str, payload: Any) -> None:
        self.events.put((kind, payload))

    def _zoom_in(self) -> str:
        for level in UI_ZOOM_LEVELS:
            if level > self.ui_zoom + 0.001:
                self._set_zoom(level)
                break
        return "break"

    def _zoom_out(self) -> str:
        for level in reversed(UI_ZOOM_LEVELS):
            if level < self.ui_zoom - 0.001:
                self._set_zoom(level)
                break
        return "break"

    def _reset_zoom(self) -> str:
        self._set_zoom(1.0)
        return "break"

    def _pick_font_family(self, candidates: tuple[str, ...], fallback: str) -> str:
        """Return the first installed scalable family, else Tk's named default.

        A family that renders as a bitmap font reports identical metrics at
        every size, so zooming would leave the text unchanged. Those are
        rejected in favour of the named default, which is always scalable.
        """
        available = {name.lower() for name in tkfont.families(self)}
        for family in candidates:
            if family.lower() in available and self._is_scalable(family):
                return family
        resolved = tkfont.nametofont(fallback).actual("family")
        if not self._is_scalable(resolved):
            print(
                f"warning: no scalable font found (using {resolved!r}); "
                "zoom will not resize text. Install a TrueType font such as "
                "DejaVu Sans.",
                file=sys.stderr,
            )
        return resolved

    def _is_scalable(self, family: str) -> bool:
        probe = tkfont.Font(self, family=family, size=10)
        small = probe.measure("Calibration")
        probe.configure(size=30)
        return probe.measure("Calibration") != small

    def _set_zoom(self, zoom: float) -> None:
        zoom = min(UI_ZOOM_LEVELS, key=lambda level: abs(level - zoom))
        self.ui_zoom = zoom
        self.zoom_text.set(f"{round(zoom * 100)}%")

        for name, font in self.ui_fonts.items():
            _family, base_size, _weight = self._font_specs[name]
            font.configure(size=max(6, round(base_size * zoom)))

        # Tk's built-in named fonts back plain tk widgets and the standard
        # dialogs, which would otherwise stay at their original size.
        for name, base_size in self._named_font_sizes.items():
            tkfont.nametofont(name).configure(size=max(6, round(base_size * zoom)))

        def scaled_pair(x: int, y: int) -> tuple[int, int]:
            return max(2, round(x * zoom)), max(2, round(y * zoom))

        style = ttk.Style(self)
        style.configure("TButton", padding=scaled_pair(10, 6))
        style.configure("Compact.TButton", padding=scaled_pair(6, 3))
        style.configure("Accent.TButton", padding=scaled_pair(12, 7))
        style.configure("CompactAccent.TButton", padding=scaled_pair(8, 3))
        style.configure("TNotebook.Tab", padding=scaled_pair(16, 9))
        style.configure("Treeview", rowheight=max(20, round(28 * zoom)))

        if hasattr(self, "controller_shell"):
            controller_width = max(300, min(1125, round(375 * zoom)))
            self.controller_shell.configure(width=controller_width)
        self.update_idletasks()
        if hasattr(self, "controller_photo_panel"):
            self._schedule_controller_photo_resize()

    def _toggle_maximize(self) -> None:
        if self._manual_maximized:
            if self._restore_geometry:
                self.geometry(self._restore_geometry)
            self._manual_maximized = False
            self.maximize_text.set("Maximize")
            return

        self.update_idletasks()
        self._restore_geometry = self.geometry()
        max_width, max_height = self.maxsize()
        self.geometry(f"{max_width}x{max_height}+0+0")
        self._manual_maximized = True
        self.maximize_text.set("Restore")

    def _scroll_controller(self, event: Any) -> Optional[str]:
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None and widget is not self.controller_shell:
            widget = getattr(widget, "master", None)
        if widget is not self.controller_shell:
            return None

        if getattr(event, "num", None) == 4:
            amount = -1
        elif getattr(event, "num", None) == 5:
            amount = 1
        else:
            delta = getattr(event, "delta", 0)
            amount = -1 if delta > 0 else 1
        self.controller_canvas.yview_scroll(amount, "units")
        return "break"

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        ui_family = self._pick_font_family(UI_FONT_CANDIDATES, "TkDefaultFont")
        mono_family = self._pick_font_family(MONO_FONT_CANDIDATES, "TkFixedFont")
        self._font_specs = {
            "body": (ui_family, 10, "normal"),
            "body_bold": (ui_family, 10, "bold"),
            "title": (ui_family, 18, "bold"),
            "page_title": (ui_family, 15, "bold"),
            "section": (ui_family, 12, "bold"),
            "metric": (ui_family, 11, "bold"),
            "small": (ui_family, 9, "normal"),
            "small_bold": (ui_family, 9, "bold"),
            "tiny_bold": (ui_family, 8, "bold"),
            "mono": (mono_family, 9, "normal"),
        }
        self.ui_fonts = {
            name: tkfont.Font(self, family=family, size=size, weight=weight)
            for name, (family, size, weight) in self._font_specs.items()
        }
        self._named_font_sizes = {}
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                named = tkfont.nametofont(name)
            except tk.TclError:
                continue
            named.configure(family=ui_family)
            size = abs(int(named.cget("size"))) or 10
            self._named_font_sizes[name] = size
        self.option_add("*Font", self.ui_fonts["body"])
        style.configure(".", background=SURFACE, foreground=INK, font=self.ui_fonts["body"])
        style.configure("TFrame", background=SURFACE)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=SURFACE, foreground=INK)
        style.configure("Panel.TLabel", background=PANEL, foreground=INK)
        style.configure("Title.TLabel", background=SURFACE, foreground=INK, font=self.ui_fonts["title"])
        style.configure("Section.TLabel", background=PANEL, foreground=INK, font=self.ui_fonts["section"])
        style.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("Status.TLabel", background=SURFACE, foreground=MUTED, font=self.ui_fonts["small"])
        style.configure("Metric.TLabel", background=PANEL, foreground=INK, font=self.ui_fonts["metric"])
        style.configure("TButton", padding=(10, 6))
        style.configure("Compact.TButton", padding=(6, 3))
        style.configure("Accent.TButton", background=ACCENT, foreground="white", padding=(12, 7))
        style.map("Accent.TButton", background=[("active", ACCENT_DARK), ("pressed", ACCENT_DARK)])
        style.configure("CompactAccent.TButton", background=ACCENT, foreground="white", padding=(8, 3))
        style.map("CompactAccent.TButton", background=[("active", ACCENT_DARK), ("pressed", ACCENT_DARK)])
        style.configure("TNotebook", background=SURFACE, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(16, 9), font=self.ui_fonts["body_bold"])
        style.configure("Card.TLabelframe", background=PANEL, borderwidth=1, relief="solid")
        style.configure("Card.TLabelframe.Label", background=PANEL, foreground=INK, font=self.ui_fonts["metric"])
        style.configure("Treeview", rowheight=28, background=PANEL, fieldbackground=PANEL, borderwidth=0)
        style.configure("Treeview.Heading", font=self.ui_fonts["small_bold"], padding=(8, 7))

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        banner = ttk.Frame(self, padding=(18, 12))
        banner.grid(row=0, column=0, sticky="ew")
        banner.columnconfigure(1, weight=1)
        title = ttk.Frame(banner)
        title.grid(row=0, column=0, rowspan=2, sticky="w")
        ttk.Label(title, text="Desk Buddy", style="Title.TLabel").pack(side="left")
        ttk.Label(title, text="CALIBRATION", foreground=ACCENT, font=self.ui_fonts["small_bold"]).pack(side="left", padx=(10, 0), pady=(8, 0))

        connection = ttk.Frame(banner)
        connection.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 18))
        ttk.Label(connection, textvariable=self.status_text, font=self.ui_fonts["body_bold"]).pack(anchor="e")
        detail = ttk.Frame(connection)
        detail.pack(anchor="e")
        ttk.Label(detail, textvariable=self.robot_text, style="Status.TLabel").pack(side="left")
        ttk.Label(detail, text="  •  ", style="Status.TLabel").pack(side="left")
        ttk.Label(detail, textvariable=self.heartbeat_text, style="Status.TLabel").pack(side="left")
        window_tools = ttk.Frame(banner)
        window_tools.grid(row=0, column=2, rowspan=2, sticky="e")
        ttk.Button(window_tools, text="−", width=3, style="Compact.TButton", command=self._zoom_out).pack(side="left")
        ttk.Label(window_tools, textvariable=self.zoom_text, width=5, anchor="center").pack(side="left", padx=2)
        ttk.Button(window_tools, text="+", width=3, style="Compact.TButton", command=self._zoom_in).pack(side="left")
        ttk.Button(window_tools, textvariable=self.maximize_text, style="Compact.TButton", command=self._toggle_maximize).pack(side="left", padx=(8, 8))
        ttk.Button(window_tools, text="Refresh", style="Accent.TButton", command=self.refresh_status).pack(side="left")

        self.main_pane = ttk.PanedWindow(self, orient="vertical")
        self.main_pane.grid(row=1, column=0, sticky="nsew")

        workspace = ttk.Frame(self.main_pane, padding=(14, 0, 14, 8))
        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(0, weight=1)
        self.main_pane.add(workspace, weight=6)

        self.notebook = ttk.Notebook(workspace)
        self.notebook.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.setup_tab = ttk.Frame(self.notebook, padding=12)
        self.status_tab = ttk.Frame(self.notebook, padding=12)
        self.base_perch_tab = ttk.Frame(self.notebook, padding=12)
        self.ik_tab = ttk.Frame(self.notebook, padding=12)
        self.visual_calibration_tab = ttk.Frame(self.notebook, padding=12)
        self.reach_and_grab_tab = ttk.Frame(self.notebook, padding=12)
        self.stencil_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.setup_tab, text="Setup")
        self.notebook.add(self.status_tab, text="Status")
        self.notebook.add(self.base_perch_tab, text="Base + Perch")
        self.notebook.add(self.ik_tab, text="IK")
        self.notebook.add(self.visual_calibration_tab, text="Visual Calibration")
        self.notebook.add(self.reach_and_grab_tab, text="Reach and Grab")
        self.notebook.add(self.stencil_tab, text="Stencil")

        self.controller_shell = ttk.Frame(workspace, style="Panel.TFrame", padding=8)
        self.controller_shell.grid(row=0, column=1, sticky="nsew")
        self.controller_shell.configure(width=375)
        self.controller_shell.grid_propagate(False)
        self.controller_shell.columnconfigure(0, weight=1)
        self.controller_shell.rowconfigure(0, weight=1)

        self.controller_canvas = tk.Canvas(
            self.controller_shell,
            background=PANEL,
            borderwidth=0,
            highlightthickness=0,
        )
        self.controller_canvas.grid(row=0, column=0, sticky="nsew")
        controller_scroll = ttk.Scrollbar(
            self.controller_shell,
            orient="vertical",
            command=self.controller_canvas.yview,
        )
        controller_scroll.grid(row=0, column=1, sticky="ns")
        self.controller_canvas.configure(yscrollcommand=controller_scroll.set)
        self.controller_content = ttk.Frame(self.controller_canvas, style="Panel.TFrame")
        self.controller_window = self.controller_canvas.create_window(
            (0, 0),
            window=self.controller_content,
            anchor="nw",
        )
        self.controller_content.bind(
            "<Configure>",
            lambda _event: self.controller_canvas.configure(
                scrollregion=self.controller_canvas.bbox("all")
            ),
        )
        self.controller_canvas.bind(
            "<Configure>",
            lambda event: self.controller_canvas.itemconfigure(
                self.controller_window,
                width=event.width,
            ),
        )
        self.bind_all("<MouseWheel>", self._scroll_controller, add="+")
        self.bind_all("<Button-4>", self._scroll_controller, add="+")
        self.bind_all("<Button-5>", self._scroll_controller, add="+")

        self._build_setup_tab()
        self._build_status_tab()
        self._build_base_perch_tab()
        self._build_ik_tab()
        self._build_visual_calibration_tab()
        self._build_reach_and_grab_tab()
        self._build_stencil_tab()
        self._build_servo_controls(self.controller_content)
        self._build_topic_log()

    def _build_setup_tab(self) -> None:
        self.setup_tab.columnconfigure(1, weight=1)
        self.setup_tab.columnconfigure(3, weight=1)
        self.vars: Dict[str, tk.Variable] = {
            "broker": tk.StringVar(),
            "port": tk.IntVar(value=8883),
            "admin_user": tk.StringVar(),
            "admin_password": tk.StringVar(),
            "robot_topic": tk.StringVar(),
            "client_id": tk.StringVar(),
            "sender": tk.StringVar(),
            "tls": tk.BooleanVar(value=True),
            "tls_insecure": tk.BooleanVar(value=False),
            "ca_cert_path": tk.StringVar(),
            "timeout": tk.DoubleVar(value=240.0),
            "save": tk.BooleanVar(value=True),
        }

        fields = [
            ("Broker", "broker", 0, 0),
            ("Port", "port", 0, 2),
            ("Admin MQTT User", "admin_user", 1, 0),
            ("Admin MQTT Password", "admin_password", 1, 2),
            ("Robot Topic Prefix", "robot_topic", 2, 0),
            ("Client ID", "client_id", 2, 2),
            ("Sender", "sender", 3, 0),
            ("Timeout Seconds", "timeout", 3, 2),
            ("CA Cert Path", "ca_cert_path", 4, 0),
        ]
        for label, key, row, col in fields:
            ttk.Label(self.setup_tab, text=label).grid(row=row, column=col, sticky="w", pady=5, padx=(0, 8))
            show = "*" if key == "admin_password" else ""
            entry = ttk.Entry(self.setup_tab, textvariable=self.vars[key], show=show)
            entry.grid(row=row, column=col + 1, sticky="ew", pady=5, padx=(0, 16))
            self._bind_edit_guard(entry)

        ttk.Button(self.setup_tab, text="Browse", command=self._browse_ca_cert).grid(row=4, column=2, sticky="w")
        ttk.Checkbutton(self.setup_tab, text="Use TLS", variable=self.vars["tls"]).grid(row=5, column=1, sticky="w", pady=8)
        ttk.Checkbutton(self.setup_tab, text="TLS insecure mode", variable=self.vars["tls_insecure"]).grid(row=5, column=2, sticky="w", pady=8)
        ttk.Checkbutton(self.setup_tab, text=f"Save settings to {ENV_PATH}", variable=self.vars["save"]).grid(row=6, column=1, columnspan=3, sticky="w")

        button_row = ttk.Frame(self.setup_tab)
        button_row.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(18, 8))
        ttk.Button(button_row, text="Save Settings", command=self.save_settings).pack(side="left", padx=(0, 8))
        ttk.Button(button_row, text="Connect", command=self.connect_mqtt).pack(side="left", padx=(0, 8))
        ttk.Button(button_row, text="Disconnect", command=self.disconnect_mqtt).pack(side="left")

        self.derived_topics = tk.StringVar()
        ttk.Label(self.setup_tab, textvariable=self.derived_topics, foreground="#555").grid(row=8, column=0, columnspan=4, sticky="w", pady=(16, 0))

    def _build_topic_log(self) -> None:
        log_frame = ttk.LabelFrame(self.main_pane, text="Activity", padding=(10, 6), style="Card.TLabelframe")
        self.main_pane.add(log_frame, weight=1)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(log_frame)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(toolbar, text="All traffic on the command and heartbeat topics", foreground=MUTED).pack(side="left")
        ttk.Button(toolbar, text="Clear", command=self._clear_log).pack(side="right", padx=(6, 0))
        ttk.Button(toolbar, text="Copy", command=self._copy_log).pack(side="right")
        ttk.Checkbutton(
            toolbar,
            text="Show heartbeats",
            variable=self.show_heartbeats,
            command=self._render_topic_log,
        ).pack(side="right", padx=(0, 12))

        body = ttk.Frame(log_frame)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.topic_log = tk.Text(
            body,
            height=5,
            wrap="none",
            state="disabled",
            borderwidth=0,
            background="#101820",
            foreground="#dce6eb",
            insertbackground="#dce6eb",
            font=self.ui_fonts["mono"],
        )
        self.topic_log.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(body, orient="vertical", command=self.topic_log.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(body, orient="horizontal", command=self.topic_log.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        self.topic_log.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.topic_log_yscroll = yscroll
        self.topic_log_xscroll = xscroll

    def _build_status_tab(self) -> None:
        self.status_tab.columnconfigure(0, weight=1)
        self.status_tab.rowconfigure(3, weight=1)

        heading = ttk.Frame(self.status_tab)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        heading.columnconfigure(0, weight=1)
        copy = ttk.Frame(heading)
        copy.grid(row=0, column=0, sticky="w")
        ttk.Label(copy, text="Calibration status", font=self.ui_fonts["page_title"]).pack(anchor="w")
        ttk.Label(
            copy,
            text="Saved values are distinguished from firmware defaults and missing calibration.",
            foreground=MUTED,
        ).pack(anchor="w", pady=(2, 0))
        actions = ttk.Frame(heading)
        actions.grid(row=0, column=1, sticky="e")
        ttk.Button(actions, text="Reconnect", command=self.connect_mqtt).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Save Summary", command=self.save_session_summary).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Refresh Status", style="Accent.TButton", command=self.refresh_status).pack(side="left")

        summary = ttk.Frame(self.status_tab)
        summary.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        for column in range(5):
            summary.columnconfigure(column, weight=1)
        summary_cards = (
            ("Overall", "overall"),
            ("Base", "base"),
            ("Perch", "perch"),
            ("IK", "ik"),
            ("Stencil", "stencil"),
        )
        for column, (label, key) in enumerate(summary_cards):
            card = ttk.Frame(summary, style="Panel.TFrame", padding=(10, 8))
            card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 5, 0 if column == 4 else 5))
            ttk.Label(card, text=label.upper(), style="Muted.TLabel", font=self.ui_fonts["tiny_bold"]).pack(anchor="w")
            ttk.Label(card, textvariable=self.status_summary_vars[key], style="Metric.TLabel").pack(anchor="w", pady=(3, 0))

        system = ttk.LabelFrame(self.status_tab, text="Robot state", padding=(10, 8), style="Card.TLabelframe")
        system.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        for column in range(4):
            system.columnconfigure(column, weight=1)
        for column, (label, key) in enumerate(
            (
                ("Firmware", "firmware"),
                ("Heartbeat", "heartbeat"),
                ("Last reset", "reset"),
                ("Last error", "error"),
            )
        ):
            cell = ttk.Frame(system, style="Panel.TFrame")
            cell.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
            ttk.Label(cell, text=label, style="Muted.TLabel").pack(anchor="w")
            ttk.Label(
                cell,
                textvariable=self.system_status_vars[key],
                style="Panel.TLabel",
                wraplength=180,
            ).pack(anchor="w", pady=(2, 0))

        checklist = ttk.LabelFrame(self.status_tab, text="Saved preferences", padding=8, style="Card.TLabelframe")
        checklist.grid(row=3, column=0, sticky="nsew")
        checklist.columnconfigure(0, weight=1)
        checklist.rowconfigure(1, weight=1)
        legend = ttk.Label(
            checklist,
            text="✓ Saved     ○ Firmware default     ! Missing required     — Optional",
            style="Muted.TLabel",
        )
        legend.grid(row=0, column=0, sticky="w", pady=(0, 6))

        columns = ("state", "preference", "value", "source")
        self.status_tree = ttk.Treeview(checklist, columns=columns, show="tree headings")
        self.status_tree.heading("#0", text="Area")
        self.status_tree.heading("state", text="State")
        self.status_tree.heading("preference", text="Preference key")
        self.status_tree.heading("value", text="Effective value")
        self.status_tree.heading("source", text="Source / next step")
        self.status_tree.column("#0", width=175, minwidth=145, stretch=False)
        self.status_tree.column("state", width=95, minwidth=85, stretch=False)
        self.status_tree.column("preference", width=205, minwidth=170)
        self.status_tree.column("value", width=285, minwidth=180)
        self.status_tree.column("source", width=160, minwidth=130)
        self.status_tree.tag_configure("group", background="#e9eef2", foreground=INK, font=self.ui_fonts["body_bold"])
        self.status_tree.tag_configure("saved", foreground=GOOD)
        self.status_tree.tag_configure("default", foreground=WARN)
        self.status_tree.tag_configure("missing", foreground=BAD)
        self.status_tree.tag_configure("optional", foreground=MUTED)
        self.status_tree.grid(row=1, column=0, sticky="nsew")
        status_scroll = ttk.Scrollbar(checklist, orient="vertical", command=self.status_tree.yview)
        status_scroll.grid(row=1, column=1, sticky="ns")
        self.status_tree.configure(yscrollcommand=status_scroll.set)

    def _build_servo_controls(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        ttk.Label(parent, text="Robot controller", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            parent,
            text="Always available · observed state updates from telemetry",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="nw", pady=(2, 8))

        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.grid(row=2, column=0, sticky="new")
        frame.columnconfigure(0, weight=1)
        for idx, name in enumerate(("ELBOW", "WRIST", "TWIST")):
            row = idx * 2
            ttk.Label(frame, text=name.title(), style="Panel.TLabel", font=self.ui_fonts["small_bold"]).grid(row=row, column=0, sticky="w", pady=(7, 1))
            ttk.Button(
                frame,
                text="−5",
                width=3,
                style="Compact.TButton",
                command=lambda n=name: self._nudge(n, -5),
            ).grid(row=row, column=1, sticky="e", padx=(4, 2), pady=(5, 0))
            ttk.Button(
                frame,
                text="+5",
                width=3,
                style="Compact.TButton",
                command=lambda n=name: self._nudge(n, 5),
            ).grid(row=row, column=2, sticky="e", padx=2, pady=(5, 0))
            ttk.Label(frame, text="STATE", style="Muted.TLabel", font=self.ui_fonts["tiny_bold"]).grid(row=row, column=3, sticky="e", padx=(8, 0), pady=(7, 1))
            ttk.Label(frame, textvariable=self.observed_angles[name], style="Metric.TLabel").grid(row=row, column=4, sticky="e", padx=(4, 0), pady=(7, 1))
            ttk.Scale(frame, from_=0, to=180, variable=self.controller_angles[name], orient="horizontal").grid(
                row=row + 1,
                column=0,
                columnspan=3,
                sticky="ew",
                padx=(0, 8),
            )
            spinbox = ttk.Spinbox(frame, from_=0, to=180, textvariable=self.controller_angles[name], width=5)
            spinbox.grid(row=row + 1, column=3, sticky="ew", padx=(0, 4))
            self._bind_edit_guard(spinbox)
            ttk.Button(
                frame,
                text="Move",
                style="Compact.TButton",
                command=lambda n=name: self.move_servo(n),
            ).grid(row=row + 1, column=4, sticky="e")

        all_row = ttk.Frame(frame, style="Panel.TFrame")
        all_row.grid(row=6, column=0, columnspan=5, sticky="ew", pady=(10, 4))
        ttk.Button(all_row, text="Sync state → targets", style="Compact.TButton", command=self.sync_controller_from_robot).pack(side="left")
        ttk.Button(all_row, text="Move all", style="CompactAccent.TButton", command=self.move_all_servos).pack(side="right")
        ttk.Checkbutton(frame, text="Live / safety bypass", variable=self.live_mode).grid(
            row=7,
            column=0,
            columnspan=5,
            sticky="w",
            pady=(2, 8),
        )

        gripper = ttk.LabelFrame(parent, text="Gripper", padding=6, style="Card.TLabelframe")
        gripper.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        for column in range(3):
            gripper.columnconfigure(column, weight=1)
        ttk.Label(gripper, text="STATE", style="Muted.TLabel", font=self.ui_fonts["tiny_bold"]).grid(row=0, column=0, sticky="w")
        ttk.Label(gripper, textvariable=self.observed_angles["GRIPPER"], style="Metric.TLabel").grid(row=0, column=1, columnspan=2, sticky="e")
        ttk.Button(gripper, text="Grab", style="Compact.TButton", command=lambda: self.run_gripper("GRAB")).grid(row=1, column=0, sticky="ew", padx=(0, 3), pady=(5, 0))
        ttk.Button(gripper, text="Soft hold", style="Compact.TButton", command=lambda: self.run_gripper("SOFTHOLD")).grid(row=1, column=1, sticky="ew", padx=3, pady=(5, 0))
        ttk.Button(gripper, text="Drop", style="Compact.TButton", command=lambda: self.run_gripper("DROP")).grid(row=1, column=2, sticky="ew", padx=(3, 0), pady=(5, 0))

        base = ttk.LabelFrame(parent, text="Base rotation", padding=6, style="Card.TLabelframe")
        base.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        base.columnconfigure(1, weight=1)
        ttk.Label(base, text="Rotation value", font=self.ui_fonts["small_bold"]).grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(0, 3),
        )
        self.base_rotation_value_entry = ttk.Spinbox(
            base,
            from_=0,
            to=9999,
            increment=1,
            textvariable=self.base_rotation_value,
        )
        self.base_rotation_value_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._bind_edit_guard(self.base_rotation_value_entry)

        ttk.Label(base, text="Direction").grid(row=2, column=0, sticky="w", pady=3)
        ttk.OptionMenu(base, self.base_direction, self.base_direction.get(), "LEFT", "RIGHT").grid(
            row=2,
            column=1,
            sticky="ew",
            padx=(6, 0),
            pady=3,
        )
        ttk.Label(base, text="Speed").grid(row=3, column=0, sticky="w", pady=3)
        ttk.OptionMenu(base, self.base_speed, self.base_speed.get(), "veryslow", "slow", "regular", "fast", "superfast").grid(
            row=3,
            column=1,
            sticky="ew",
            padx=(6, 0),
            pady=3,
        )

        rotation_actions = ttk.Frame(base, style="Panel.TFrame")
        rotation_actions.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        rotation_actions.columnconfigure(0, weight=1)
        ttk.Button(
            rotation_actions,
            text="Go to absolute angle",
            style="Compact.TButton",
            command=self.move_base_angle,
        ).grid(row=0, column=0, sticky="ew", pady=2)
        ttk.Button(
            rotation_actions,
            text="Rotate relative degrees",
            style="CompactAccent.TButton",
            command=self.move_base_degrees,
        ).grid(row=1, column=0, sticky="ew", pady=2)
        ttk.Button(
            rotation_actions,
            text="Rotate firmware steps",
            style="CompactAccent.TButton",
            command=self.move_base_steps,
        ).grid(row=2, column=0, sticky="ew", pady=2)

        ttk.Button(base, text="Read state", style="Compact.TButton", command=self.base_status).grid(row=5, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(
            base,
            textvariable=self.observed_base_text,
            foreground=MUTED,
            wraplength=310,
        ).grid(row=5, column=1, sticky="w", padx=(6, 0), pady=(8, 0))

        self.controller_photo_panel = ttk.LabelFrame(
            parent,
            text="Camera",
            padding=6,
            style="Card.TLabelframe",
        )
        self.controller_photo_panel.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        self.controller_photo_panel.columnconfigure(0, weight=1)
        self.controller_photo_label = ttk.Label(
            self.controller_photo_panel,
            text="No photo captured yet",
            anchor="center",
        )
        self.controller_photo_label.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.controller_capture_button = ttk.Button(
            self.controller_photo_panel,
            text="Capture Photo",
            style="CompactAccent.TButton",
            command=lambda: self.capture_photo("controller"),
        )
        self.controller_capture_button.grid(row=1, column=0, sticky="ew")
        self.controller_photo_panel.bind(
            "<Configure>",
            lambda _event: self._schedule_controller_photo_resize(),
        )

    def _build_photo_panel(self, parent: ttk.Frame, row: int, column: int) -> None:
        panel = ttk.LabelFrame(parent, text="Camera", padding=10)
        panel.grid(row=row, column=column, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        photo_label = ttk.Label(panel, text="No photo captured yet", anchor="center")
        photo_label.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        self.photo_labels.append(photo_label)
        panel.rowconfigure(0, weight=1)
        ttk.Button(panel, text="Capture Photo", command=lambda: self.capture_photo("manual")).grid(row=1, column=0, sticky="ew")

    def _build_base_perch_tab(self) -> None:
        self.base_perch_tab.columnconfigure(0, weight=1)
        self.base_perch_tab.rowconfigure(1, weight=1)

        heading = ttk.Frame(self.base_perch_tab)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(heading, text="Base + perch", font=self.ui_fonts["page_title"]).pack(anchor="w")
        ttk.Label(
            heading,
            text="Calibrate the rotating base first, then save a safe resting pose for the arm.",
            foreground=MUTED,
        ).pack(anchor="w", pady=(2, 0))

        sections = ttk.PanedWindow(self.base_perch_tab, orient="vertical")
        sections.grid(row=1, column=0, sticky="nsew")

        base = ttk.LabelFrame(sections, text="1 · Base rotation", padding=12, style="Card.TLabelframe")
        base.columnconfigure(1, weight=1)
        ttk.Label(
            base,
            text="The profile measures full revolutions and establishes a trusted absolute position.",
            style="Muted.TLabel",
            wraplength=720,
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))
        ttk.Label(base, text="Neutral servo angle").grid(row=1, column=0, sticky="w")
        # Firmware rejects anything outside 70..110, so bound the entry there
        # rather than letting a run start and fail minutes later.
        neutral_entry = ttk.Spinbox(base, from_=70, to=110, textvariable=self.base_neutral, width=6)
        neutral_entry.grid(row=1, column=1, sticky="w", padx=(8, 16))
        self._bind_edit_guard(neutral_entry)
        ttk.Button(base, text="Run profile calibration", style="Accent.TButton", command=self.run_base_profile).grid(
            row=1,
            column=2,
            sticky="w",
            padx=(0, 8),
        )
        ttk.Button(base, text="Read base state", command=self.base_status).grid(row=1, column=3, sticky="w")
        ttk.Label(base, textvariable=self.observed_base_text, foreground=MUTED, wraplength=760).grid(
            row=2,
            column=0,
            columnspan=4,
            sticky="w",
            pady=(10, 0),
        )
        sections.add(base, weight=1)

        perch = ttk.LabelFrame(sections, text="2 · Perch pose", padding=12, style="Card.TLabelframe")
        perch.columnconfigure(0, weight=1)
        perch.columnconfigure(1, weight=1)
        perch.rowconfigure(1, weight=1)

        instructions = ttk.Frame(perch, style="Panel.TFrame")
        instructions.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        ttk.Label(
            instructions,
            text="Use the controller on the right to position the arm. Copy those targets below, save them, then test the saved perch.",
            style="Muted.TLabel",
            wraplength=650,
        ).pack(side="left")

        pose = ttk.LabelFrame(perch, text="Pose values", padding=10, style="Card.TLabelframe")
        pose.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        pose.columnconfigure(1, weight=1)
        for idx, name in enumerate(("ELBOW", "WRIST", "TWIST")):
            ttk.Label(pose, text=name.title()).grid(row=idx, column=0, sticky="w", pady=4)
            entry = ttk.Spinbox(pose, from_=0, to=180, textvariable=self.perch_angle_vars[name], width=7)
            entry.grid(row=idx, column=1, sticky="ew", padx=(8, 0), pady=4)
            self._bind_edit_guard(entry)
        ttk.Button(pose, text="Copy controller targets", command=self.use_controller_for_perch).grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(10, 4),
        )
        ttk.Button(pose, text="Save perch pose", style="Accent.TButton", command=self.save_perch_angles).grid(
            row=4,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=4,
        )
        ttk.Button(pose, text="Move to saved perch", command=self.move_saved_perch).grid(
            row=5,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=4,
        )

        optional = ttk.LabelFrame(perch, text="Optional reach landmarks", padding=10, style="Card.TLabelframe")
        optional.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        optional.columnconfigure(1, weight=1)
        self.perch_dist_vars = {
            "min": tk.DoubleVar(value=0),
            "mid": tk.DoubleVar(value=60),
            "max": tk.DoubleVar(value=120),
        }
        for idx, kind in enumerate(("min", "mid", "max")):
            ttk.Label(optional, text=kind.title()).grid(row=idx, column=0, sticky="w", pady=4)
            entry = ttk.Entry(optional, textvariable=self.perch_dist_vars[kind], width=8)
            entry.grid(row=idx, column=1, sticky="ew", padx=(8, 4), pady=4)
            ttk.Label(optional, text="mm", foreground=MUTED).grid(row=idx, column=2, sticky="w")
            self._bind_edit_guard(entry)
        ttk.Button(optional, text="Save reach landmarks", command=self.save_perch_distances).grid(
            row=3,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(10, 4),
        )
        ttk.Button(optional, text="Capture perch photo", command=lambda: self.capture_photo("perch")).grid(
            row=4,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=4,
        )
        ttk.Label(
            optional,
            text="These landmarks are optional and do not replace the IK calibration points.",
            foreground=MUTED,
            wraplength=300,
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))
        sections.add(perch, weight=3)

    def _build_ik_tab(self) -> None:
        self.ik_tab.columnconfigure(0, weight=1)
        self.ik_tab.columnconfigure(1, weight=1)
        self.ik_tab.rowconfigure(1, weight=1)
        top = ttk.LabelFrame(self.ik_tab, text="IK Workflow", padding=10)
        top.grid(row=0, column=0, sticky="nsew", padx=(0, 12), pady=(0, 8))
        top.columnconfigure(0, weight=1)
        ttk.Label(
            top,
            text=(
                "Send a calibrated IK target directly, or set the arm with the persistent controller "
                "and copy its targets into a calibration row."
            ),
            foreground=MUTED,
            wraplength=420,
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))

        direct = ttk.LabelFrame(top, text="Direct IK control", padding=8)
        direct.grid(row=1, column=0, sticky="ew", pady=(2, 6))
        direct.columnconfigure(1, weight=1)
        ttk.Label(direct, text="Y distance").grid(row=0, column=0, sticky="w", pady=3)
        y_entry = ttk.Spinbox(
            direct,
            from_=0,
            to=1000,
            increment=1,
            textvariable=self.ik_control_y,
            width=8,
        )
        y_entry.grid(row=0, column=1, sticky="ew", padx=(8, 4), pady=3)
        ttk.Label(direct, text="mm", foreground=MUTED).grid(row=0, column=2, sticky="w", pady=3)
        self._bind_edit_guard(y_entry)

        ttk.Label(direct, text="Z height").grid(row=1, column=0, sticky="w", pady=3)
        z_entry = ttk.Spinbox(
            direct,
            from_=0,
            to=50,
            increment=1,
            textvariable=self.ik_control_z,
            width=8,
        )
        z_entry.grid(row=1, column=1, sticky="ew", padx=(8, 4), pady=3)
        ttk.Label(direct, text="mm", foreground=MUTED).grid(row=1, column=2, sticky="w", pady=3)
        self._bind_edit_guard(z_entry)
        ttk.Button(
            direct,
            text="Send IK command",
            style="Accent.TButton",
            command=self.send_ik_control,
        ).grid(row=2, column=0, columnspan=3, sticky="ew", pady=(7, 0))
        ttk.Label(
            direct,
            text="Publishes controlik with distance=Y and z_height=Z. Firmware applies the saved IK calibration.",
            foreground=MUTED,
            wraplength=390,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

        ttk.Button(top, text="Move to controller targets", command=self.move_all_servos).grid(row=2, column=0, sticky="ew", pady=4)
        ttk.Button(top, text="Capture IK photo", command=lambda: self.capture_photo("ik")).grid(row=3, column=0, sticky="ew", pady=4)
        self._build_photo_panel(self.ik_tab, 0, 1)

        self.ik_rows: Dict[str, Dict[str, Dict[str, tk.Variable]]] = {}
        canvas = tk.Canvas(self.ik_tab, highlightthickness=0)
        scroll = ttk.Scrollbar(self.ik_tab, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=1, column=0, columnspan=2, sticky="nsew")
        scroll.grid(row=1, column=2, sticky="ns")

        row = 0
        for plane, title in (("z0", "z=0 saved firmware points"), ("z50", "z=50 saved firmware points"), ("z25", "z=25 validation only")):
            group = ttk.LabelFrame(inner, text=title, padding=10)
            group.grid(row=row, column=0, sticky="ew", pady=(0, 12))
            group.columnconfigure(7, weight=1)
            self.ik_rows[plane] = {}
            headers = ["Point", "Distance", "ELBOW", "WRIST", "TWIST", "Robot Controller", "Result"]
            for col, header in enumerate(headers):
                ttk.Label(group, text=header, font=self.ui_fonts["small_bold"]).grid(row=0, column=col, sticky="w", padx=4)
            for idx, kind in enumerate(("min", "mid", "max"), start=1):
                defaults_by_plane = {
                    "z0": {"min": 0, "mid": 60, "max": 120},
                    "z25": {"min": 15, "mid": 67.5, "max": 120},
                    "z50": {"min": 30, "mid": 75, "max": 120},
                }
                defaults = defaults_by_plane[plane]
                vars_for_row: Dict[str, tk.Variable] = {
                    "distance": tk.DoubleVar(value=defaults[kind]),
                    "elbow": tk.IntVar(value=self.controller_angles["ELBOW"].get()),
                    "wrist": tk.IntVar(value=self.controller_angles["WRIST"].get()),
                    "twist": tk.IntVar(value=self.controller_angles["TWIST"].get()),
                    "result": tk.StringVar(value="Not recorded"),
                }
                self.ik_rows[plane][kind] = vars_for_row
                ttk.Label(group, text=kind.upper()).grid(row=idx, column=0, sticky="w", padx=4, pady=4)
                for col, key in enumerate(("distance", "elbow", "wrist", "twist"), start=1):
                    entry = ttk.Entry(group, textvariable=vars_for_row[key], width=8)
                    entry.grid(row=idx, column=col, sticky="w", padx=4, pady=4)
                    self._bind_edit_guard(entry)
                ttk.Button(group, text="Import", command=lambda p=plane, k=kind: self.use_controller_for_ik(p, k)).grid(row=idx, column=5, padx=2)
                ttk.Button(group, text="Move", command=lambda p=plane, k=kind: self.move_ik_row(p, k)).grid(row=idx, column=6, padx=2)
                if plane in ("z0", "z50"):
                    ttk.Button(group, text="Save", command=lambda p=plane, k=kind: self.save_ik_row(p, k)).grid(row=idx, column=7, padx=2, sticky="w")
                else:
                    ttk.Button(group, text="Run IK", command=lambda p=plane, k=kind: self.run_ik_validation(p, k)).grid(row=idx, column=7, padx=2, sticky="w")
                    ttk.Button(group, text="Pass", command=lambda p=plane, k=kind: self.mark_ik_validation(p, k, True)).grid(row=idx, column=8, padx=2)
                    ttk.Button(group, text="Fail", command=lambda p=plane, k=kind: self.mark_ik_validation(p, k, False)).grid(row=idx, column=9, padx=2)
                ttk.Label(group, textvariable=vars_for_row["result"]).grid(row=idx, column=10, sticky="w", padx=8)
            row += 1

    def _build_visual_calibration_tab(self) -> None:
        self.visual_calibration_tab.columnconfigure(0, weight=1)
        self.visual_calibration_tab.columnconfigure(1, weight=1)
        self.visual_calibration_tab.rowconfigure(1, weight=1)

        instructions = ttk.LabelFrame(
            self.visual_calibration_tab,
            text="Visual AI calibration",
            padding=12,
            style="Card.TLabelframe",
        )
        instructions.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 10))
        instructions.columnconfigure(1, weight=1)
        ttk.Label(
            instructions,
            text=(
                "Place the visual calibration target in the camera's working area. "
                "This sends calibrate_depth so firmware captures a fresh photo and "
                "the Vision server stores a new calibration grid."
            ),
            foreground=MUTED,
            wraplength=500,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Label(instructions, text="Magnet position").grid(
            row=1, column=0, sticky="w", padx=(0, 8)
        )
        magnet_entry = ttk.Spinbox(
            instructions,
            from_=0,
            to=999,
            textvariable=self.visual_calibration_magnet_position,
            width=10,
        )
        magnet_entry.grid(row=1, column=1, sticky="w")
        self._bind_edit_guard(magnet_entry)
        ttk.Button(
            instructions,
            text="Capture Visual Calibration",
            style="Accent.TButton",
            command=self.run_visual_calibration,
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 8))
        ttk.Label(
            instructions,
            textvariable=self.visual_calibration_status_text,
            wraplength=500,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w")

        preview = ttk.LabelFrame(
            self.visual_calibration_tab,
            text="Calibration photo",
            padding=10,
            style="Card.TLabelframe",
        )
        preview.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 10))
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.visual_calibration_photo_label = ttk.Label(
            preview,
            text="No visual calibration photo captured yet",
            anchor="center",
            justify="center",
        )
        self.visual_calibration_photo_label.grid(row=0, column=0, sticky="nsew")

        results = ttk.LabelFrame(
            self.visual_calibration_tab,
            text="Vision server result",
            padding=10,
            style="Card.TLabelframe",
        )
        results.grid(row=1, column=0, columnspan=2, sticky="nsew")
        results.columnconfigure(0, weight=1)
        results.rowconfigure(0, weight=1)
        self.visual_calibration_result_box = tk.Text(
            results,
            height=14,
            wrap="word",
            font=self.ui_fonts["mono"],
        )
        self.visual_calibration_result_box.grid(row=0, column=0, sticky="nsew")
        result_scroll = ttk.Scrollbar(
            results,
            orient="vertical",
            command=self.visual_calibration_result_box.yview,
        )
        result_scroll.grid(row=0, column=1, sticky="ns")
        self.visual_calibration_result_box.configure(
            yscrollcommand=result_scroll.set,
            state="disabled",
        )
        self._set_visual_calibration_result_text(
            "No result yet. Connect to MQTT and capture a visual calibration image."
        )

    def _build_reach_and_grab_tab(self) -> None:
        self.reach_and_grab_tab.columnconfigure(0, weight=1)
        self.reach_and_grab_tab.columnconfigure(1, weight=1)
        self.reach_and_grab_tab.rowconfigure(1, weight=1)

        request = ttk.LabelFrame(
            self.reach_and_grab_tab,
            text="Automatic reach-and-grab request",
            padding=12,
            style="Card.TLabelframe",
        )
        request.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 10))
        request.columnconfigure(1, weight=1)
        request.columnconfigure(3, weight=1)
        ttk.Label(
            request,
            text=(
                "Describe one object. The GUI sends one detect_object request and then only "
                "monitors firmware and Vision server progress. The Vision server owns all "
                "base, IK, gripper, and telemetry child commands."
            ),
            foreground=MUTED,
            wraplength=560,
            justify="left",
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 12))

        ttk.Label(request, text="Object description").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        target_entry = ttk.Entry(request, textvariable=self.reach_and_grab_target)
        target_entry.grid(row=1, column=1, columnspan=3, sticky="ew", pady=4)
        self._bind_edit_guard(target_entry)

        ttk.Checkbutton(
            request,
            text="Use configured learned model",
            variable=self.reach_and_grab_use_model,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Label(request, text="Model name (optional)").grid(row=2, column=2, sticky="e", padx=(8, 8), pady=4)
        model_entry = ttk.Entry(request, textvariable=self.reach_and_grab_model_name)
        model_entry.grid(row=2, column=3, sticky="ew", pady=4)
        self._bind_edit_guard(model_entry)

        ttk.Label(request, text="Box threshold").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=4)
        box_entry = ttk.Spinbox(
            request,
            from_=0.0,
            to=1.0,
            increment=0.05,
            textvariable=self.reach_and_grab_box_threshold,
            width=9,
        )
        box_entry.grid(row=3, column=1, sticky="w", pady=4)
        self._bind_edit_guard(box_entry)
        ttk.Label(request, text="Text threshold").grid(row=3, column=2, sticky="e", padx=(8, 8), pady=4)
        text_entry = ttk.Spinbox(
            request,
            from_=0.0,
            to=1.0,
            increment=0.05,
            textvariable=self.reach_and_grab_text_threshold,
            width=9,
        )
        text_entry.grid(row=3, column=3, sticky="w", pady=4)
        self._bind_edit_guard(text_entry)

        ttk.Label(request, text="Magnet position").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=4)
        magnet_entry = ttk.Spinbox(
            request,
            from_=0,
            to=999,
            textvariable=self.reach_and_grab_magnet_position,
            width=9,
        )
        magnet_entry.grid(row=4, column=1, sticky="w", pady=4)
        self._bind_edit_guard(magnet_entry)

        ttk.Label(request, text="Workflow ID (optional)").grid(row=5, column=0, sticky="w", padx=(0, 8), pady=4)
        workflow_entry = ttk.Entry(request, textvariable=self.reach_and_grab_workflow_id, width=12)
        workflow_entry.grid(row=5, column=1, sticky="ew", pady=4)
        self._bind_edit_guard(workflow_entry)
        ttk.Label(request, text="Event ID (optional)").grid(row=5, column=2, sticky="e", padx=(8, 8), pady=4)
        event_entry = ttk.Entry(request, textvariable=self.reach_and_grab_workflow_event_id, width=12)
        event_entry.grid(row=5, column=3, sticky="ew", pady=4)
        self._bind_edit_guard(event_entry)

        self.reach_and_grab_button = ttk.Button(
            request,
            text="Detect, Reach, and Grab",
            style="Accent.TButton",
            command=self.run_reach_and_grab,
        )
        self.reach_and_grab_button.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(14, 8))
        ttk.Label(request, textvariable=self.reach_and_grab_action_text, foreground=MUTED).grid(
            row=7, column=0, columnspan=4, sticky="w"
        )
        ttk.Label(
            request,
            textvariable=self.reach_and_grab_status_text,
            wraplength=560,
            justify="left",
        ).grid(row=8, column=0, columnspan=4, sticky="w", pady=(3, 0))

        preview = ttk.LabelFrame(
            self.reach_and_grab_tab,
            text="Detection photo",
            padding=10,
            style="Card.TLabelframe",
        )
        preview.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 10))
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.reach_and_grab_photo_label = ttk.Label(
            preview,
            text="No reach-and-grab photo received yet",
            anchor="center",
            justify="center",
        )
        self.reach_and_grab_photo_label.grid(row=0, column=0, sticky="nsew")

        results = ttk.LabelFrame(
            self.reach_and_grab_tab,
            text="Vision and robot execution progress",
            padding=10,
            style="Card.TLabelframe",
        )
        results.grid(row=1, column=0, columnspan=2, sticky="nsew")
        results.columnconfigure(0, weight=1)
        results.rowconfigure(0, weight=1)
        self.reach_and_grab_result_box = tk.Text(
            results,
            height=15,
            wrap="word",
            font=self.ui_fonts["mono"],
        )
        self.reach_and_grab_result_box.grid(row=0, column=0, sticky="nsew")
        result_scroll = ttk.Scrollbar(
            results,
            orient="vertical",
            command=self.reach_and_grab_result_box.yview,
        )
        result_scroll.grid(row=0, column=1, sticky="ns")
        self.reach_and_grab_result_box.configure(
            yscrollcommand=result_scroll.set,
            state="disabled",
        )
        self._set_reach_and_grab_result_text(
            "No request yet. Connect to MQTT, describe an object, and start reach-and-grab."
        )

    def _build_stencil_tab(self) -> None:
        self.stencil_tab.columnconfigure(0, weight=1)
        self.stencil_tab.rowconfigure(2, weight=1)

        controls = ttk.LabelFrame(self.stencil_tab, text="Stencil Session", padding=10)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        for col in range(6):
            controls.columnconfigure(col, weight=1)
        ttk.Button(controls, text="Start Session", command=self.stencil_start).grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=3)
        ttk.Button(controls, text="Run Current Point", command=self.stencil_run_point).grid(row=0, column=1, sticky="ew", padx=6, pady=3)
        ttk.Button(controls, text="Status", command=self.stencil_status_request).grid(row=0, column=2, sticky="ew", padx=6, pady=3)
        ttk.Button(controls, text="Cancel", command=self.stencil_cancel).grid(row=0, column=3, sticky="ew", padx=6, pady=3)
        ttk.Button(controls, text="Clear Saved Stencil", command=self.stencil_clear).grid(row=0, column=4, sticky="ew", padx=(6, 0), pady=3)

        adjust = ttk.LabelFrame(self.stencil_tab, text="Adjustment For Current Point", padding=10)
        adjust.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        adjust.columnconfigure(1, weight=1)
        adjust.columnconfigure(3, weight=1)
        ttk.Label(adjust, text="Rotation nudge degrees").grid(row=0, column=0, sticky="w", padx=(0, 6))
        rotation_entry = ttk.Entry(adjust, textvariable=self.stencil_rotation_nudge, width=10)
        rotation_entry.grid(row=0, column=1, sticky="ew", padx=(0, 16))
        self._bind_edit_guard(rotation_entry)
        ttk.Label(adjust, text="Distance nudge mm").grid(row=0, column=2, sticky="w", padx=(0, 6))
        distance_entry = ttk.Entry(adjust, textvariable=self.stencil_distance_nudge, width=10)
        distance_entry.grid(row=0, column=3, sticky="ew", padx=(0, 16))
        self._bind_edit_guard(distance_entry)
        ttk.Button(adjust, text="Apply Adjustment", command=self.stencil_adjust).grid(row=0, column=4, sticky="ew", padx=(0, 6))
        ttk.Button(adjust, text="Adjust & Retry Previous", command=self.stencil_adjust_previous_retry).grid(row=0, column=5, sticky="ew")

        status = ttk.Frame(self.stencil_tab)
        status.grid(row=2, column=0, sticky="nsew")
        status.columnconfigure(0, weight=1)
        status.columnconfigure(1, weight=1)
        status.rowconfigure(0, weight=1)

        summary = ttk.LabelFrame(status, text="Current Status", padding=8)
        summary.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        summary.columnconfigure(0, weight=1)
        summary.rowconfigure(0, weight=1)
        self.stencil_status_box = tk.Text(summary, height=18, wrap="word")
        self.stencil_status_box.grid(row=0, column=0, sticky="nsew")
        summary_scroll = ttk.Scrollbar(summary, orient="vertical", command=self.stencil_status_box.yview)
        summary_scroll.grid(row=0, column=1, sticky="ns")
        self.stencil_status_box.configure(yscrollcommand=summary_scroll.set)

        points = ttk.LabelFrame(status, text="Point progress", padding=8)
        points.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        points.columnconfigure(0, weight=1)
        points.rowconfigure(0, weight=1)
        self.stencil_points_box = tk.Text(points, height=18, wrap="none")
        self.stencil_points_box.grid(row=0, column=0, sticky="nsew")
        points_yscroll = ttk.Scrollbar(points, orient="vertical", command=self.stencil_points_box.yview)
        points_yscroll.grid(row=0, column=1, sticky="ns")
        points_xscroll = ttk.Scrollbar(points, orient="horizontal", command=self.stencil_points_box.xview)
        points_xscroll.grid(row=1, column=0, sticky="ew")
        self.stencil_points_box.configure(yscrollcommand=points_yscroll.set, xscrollcommand=points_xscroll.set)
        self._render_stencil_response({})

    def _load_settings_into_form(self) -> None:
        config = self.config_values
        for key in ("broker", "admin_user", "admin_password", "robot_topic", "client_id", "sender", "ca_cert_path"):
            self.vars[key].set(getattr(config, key))
        self.vars["port"].set(config.port)
        self.vars["timeout"].set(config.timeout)
        self.vars["tls"].set(config.tls)
        self.vars["tls_insecure"].set(config.tls_insecure)
        self._update_derived_topics()

    def _config_from_form(self) -> WizardConfig:
        config = WizardConfig(
            broker=str(self.vars["broker"].get()).strip(),
            port=self._safe_int(self.vars["port"].get(), 8883),
            admin_user=str(self.vars["admin_user"].get()).strip(),
            admin_password=str(self.vars["admin_password"].get()),
            robot_topic=str(self.vars["robot_topic"].get()).strip().strip("/"),
            client_id=str(self.vars["client_id"].get()).strip() or "desk-buddy-calibration-wizard",
            sender=str(self.vars["sender"].get()).strip() or "calibration_wizard",
            tls=bool(self.vars["tls"].get()),
            tls_insecure=bool(self.vars["tls_insecure"].get()),
            ca_cert_path=str(self.vars["ca_cert_path"].get()).strip() or str(DEFAULT_CA_CERT),
            timeout=self._safe_float(self.vars["timeout"].get(), 240.0),
        )
        return config

    def _safe_int(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError, tk.TclError):
            return default

    def _safe_float(self, value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError, tk.TclError):
            return default

    def _browse_ca_cert(self) -> None:
        path = filedialog.askopenfilename(title="Select MQTT CA certificate")
        if path:
            self.vars["ca_cert_path"].set(path)

    def _bind_edit_guard(self, widget: tk.Widget) -> None:
        for event_name in ("<FocusIn>", "<KeyPress>", "<ButtonPress>"):
            widget.bind(event_name, self._mark_user_editing, add="+")

    def _mark_user_editing(self, event: Any = None) -> None:
        self.user_editing_until = time.time() + 3.0

    def _is_user_editing(self) -> bool:
        focused = self.focus_get()
        if focused is not None:
            widget_class = focused.winfo_class()
            if widget_class in ("Entry", "TEntry", "Spinbox", "TSpinbox"):
                return True
        return time.time() < self.user_editing_until

    def _update_derived_topics(self) -> None:
        config = self._config_from_form()
        self.derived_topics.set(f"Command topic: {config.command_topic}    Heartbeat topic: {config.heartbeat_topic}")

    def save_settings(self) -> None:
        config = self._config_from_form()
        save_config(config)
        self.config_values = config
        self._update_derived_topics()
        self._info(f"Settings saved to {ENV_PATH}")

    def connect_mqtt(self) -> None:
        config = self._config_from_form()
        self.config_values = config
        self._update_derived_topics()
        if self.vars["save"].get():
            save_config(config)
        self._run_worker("connect", lambda: self.robot.connect(config), on_success=lambda _: self.refresh_status())

    def disconnect_mqtt(self) -> None:
        self.robot.disconnect()
        self.status_text.set("Disconnected")

    def refresh_status(self) -> None:
        self._run_worker("refresh status", self.robot.refresh_calibrationvalues)

    def _nudge(self, servo: str, delta: int) -> None:
        value = max(0, min(180, self.controller_angles[servo].get() + delta))
        self.controller_angles[servo].set(value)

    def sync_controller_from_robot(self) -> None:
        for servo in ("ELBOW", "WRIST", "TWIST"):
            observed = self._parse_observed_number(self.observed_angles[servo].get())
            if observed is not None:
                self.controller_angles[servo].set(max(0, min(180, int(round(observed)))))
        self.last_result_text.set("Controller synced from latest robot telemetry")

    def _parse_observed_number(self, text: str) -> Optional[float]:
        try:
            return float(str(text).split()[0])
        except (IndexError, TypeError, ValueError):
            return None

    def move_servo(self, servo: str) -> None:
        value = self.controller_angles[servo].get()
        self._run_worker(f"move {servo}", lambda: self.robot.request(servo_payload(self.config_values.sender, servo, value, self.live_mode.get())))

    def move_all_servos(self) -> None:
        def work() -> None:
            for servo in ("WRIST", "ELBOW", "TWIST"):
                self.robot.request(servo_payload(self.config_values.sender, servo, self.controller_angles[servo].get(), self.live_mode.get()))
        self._run_worker("move all servos", work)

    def run_gripper(self, command: str) -> None:
        self._run_worker(f"gripper {command.lower()}", lambda: self.robot.request(gripper_payload(self.config_values.sender, command)))

    def run_base_profile(self) -> None:
        if not messagebox.askyesno(
            "Base Profile Calibration",
            "Base profile calibration may rotate the base multiple full turns. Continue?",
        ):
            return
        neutral = self.base_neutral.get()

        def work() -> Dict[str, Any]:
            response = self.robot.request(
                base_profile_payload(self.config_values.sender, neutral),
                timeout=900,
            )
            if str(response.get("status") or "").lower() != "completed":
                base = response.get("base_rotation")
                firmware_error = base.get("error") if isinstance(base, dict) else None
                raise RuntimeError(
                    "Base profile calibration failed: "
                    f"{firmware_error or response.get('error') or 'firmware returned failed'}"
                )
            return response

        self._run_worker(
            "base profile calibration",
            work,
            self._show_base_profile_result,
        )

    def _show_base_profile_result(self, response: Dict[str, Any]) -> None:
        # Calibration telemetry is flat on base_rotation; there is no longer a
        # separate veryslow measurement phase, so verySlowValidated just mirrors
        # whether usable counts exist.
        base = response.get("base_rotation")
        if not isinstance(base, dict):
            self._info("Base profile completed, but the firmware did not return base rotation telemetry.")
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
        self.last_result_text.set(
            "Base profile and veryslow verification passed"
            if validated
            else "Base profile completed, veryslow verification failed"
        )
        messagebox.showinfo("Base Profile Result", summary)

    def base_status(self) -> None:
        self._run_worker("base status", lambda: self.robot.request(base_status_payload(self.config_values.sender)))

    def move_base_angle(self) -> None:
        try:
            angle = int(self.base_rotation_value.get())
        except (tk.TclError, TypeError, ValueError):
            messagebox.showerror("Calibration Wizard", "Enter a whole-number rotation value.")
            return
        if angle < 0 or angle >= 360:
            messagebox.showerror("Calibration Wizard", "Absolute base angle must be between 0 and 359 degrees.")
            return
        self._run_worker("move base angle", lambda: self.robot.request(base_angle_payload(self.config_values.sender, angle, self.base_speed.get())))

    def move_base_degrees(self) -> None:
        try:
            degrees = int(self.base_rotation_value.get())
        except (tk.TclError, TypeError, ValueError):
            messagebox.showerror("Calibration Wizard", "Enter a whole-number rotation value.")
            return
        if degrees <= 0:
            messagebox.showerror("Calibration Wizard", "Base degrees must be greater than 0.")
            return
        self._run_worker(
            "move base degrees",
            lambda: self.robot.request(base_degrees_payload(self.config_values.sender, self.base_direction.get(), degrees, self.base_speed.get())),
        )

    def move_base_steps(self) -> None:
        try:
            steps = int(self.base_rotation_value.get())
        except (tk.TclError, TypeError, ValueError):
            messagebox.showerror("Calibration Wizard", "Enter a whole-number rotation value.")
            return
        if steps <= 0:
            messagebox.showerror("Calibration Wizard", "Base steps must be greater than 0.")
            return
        self._run_worker(
            "move base steps",
            lambda: self.robot.request(base_steps_payload(self.config_values.sender, self.base_direction.get(), steps, self.base_speed.get())),
        )

    def move_saved_perch(self) -> None:
        self._run_worker("move saved perch", lambda: self.robot.request(perch_payload(self.config_values.sender)))

    def capture_photo(self, label: str) -> None:
        def work() -> Path:
            return self.robot.capture_photo(label, CAPTURE_DIR)
        self._run_worker(f"capture {label} photo", work, on_success=self._display_photo)

    def run_visual_calibration(self) -> None:
        try:
            magnet_position = int(self.visual_calibration_magnet_position.get())
        except (TypeError, ValueError, tk.TclError):
            messagebox.showerror(
                "Visual Calibration",
                "Magnet position must be a whole number.",
            )
            return
        if magnet_position < 0:
            messagebox.showerror(
                "Visual Calibration",
                "Magnet position must be zero or greater.",
            )
            return

        self.visual_calibration_status_text.set(
            "Capturing a fresh camera image and waiting for the Vision server…"
        )
        self._set_visual_calibration_result_text(
            "Waiting for firmware photo and Visual AI calibration result…"
        )
        self._run_worker(
            "visual calibration",
            lambda: self.robot.capture_visual_calibration(
                CAPTURE_DIR,
                magnet_position=magnet_position,
            ),
            on_success=self._render_visual_calibration_result,
        )

    def _set_visual_calibration_result_text(self, text: str) -> None:
        if not hasattr(self, "visual_calibration_result_box"):
            return
        self.visual_calibration_result_box.configure(state="normal")
        self.visual_calibration_result_box.delete("1.0", tk.END)
        self.visual_calibration_result_box.insert(tk.END, text)
        self.visual_calibration_result_box.configure(state="disabled")

    def _render_visual_calibration_result(
        self,
        capture: VisualCalibrationCapture,
    ) -> None:
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
            self.status_text.set("Done: visual calibration")
            self.visual_calibration_status_text.set(
                f"Completed — {point_count} calibration points saved"
                + (f" as Vision image {image_id}" if image_id is not None else "")
                + "."
            )
            lines.extend(["", f"Calibration points ({point_count}):"])
            if isinstance(points, dict) and points:
                for name, values in points.items():
                    lines.append(
                        f"{name}: {json.dumps(values, sort_keys=True, separators=(',', ':'))}"
                    )
            else:
                lines.append("The server completed without returning point details.")
        else:
            error = str(response.get("error") or "Visual AI calibration failed")
            self.status_text.set("Error: visual calibration")
            self.visual_calibration_status_text.set(f"Failed — {error}")
            lines.extend(["", f"Error: {error}"])

        self._set_visual_calibration_result_text("\n".join(lines))
        self.session["visual_calibration"] = {
            "photo": str(capture.photo_path),
            "response": response,
        }
        self.last_result_text.set(
            "Visual calibration completed"
            if status == "completed"
            else "Visual calibration failed"
        )

    def run_reach_and_grab(self) -> None:
        if self.reach_and_grab_running:
            messagebox.showinfo(
                "Reach and Grab",
                "A reach-and-grab request from this GUI is already in progress.",
            )
            return
        if not self.robot.state.broker_connected:
            messagebox.showerror("Reach and Grab", "Connect to MQTT before starting.")
            return

        target = self.reach_and_grab_target.get().strip()
        if not target:
            messagebox.showerror("Reach and Grab", "Enter a nonempty object description.")
            return

        try:
            box_threshold = float(self.reach_and_grab_box_threshold.get())
            text_threshold = float(self.reach_and_grab_text_threshold.get())
            magnet_position = int(self.reach_and_grab_magnet_position.get())
        except (TypeError, ValueError, tk.TclError):
            messagebox.showerror(
                "Reach and Grab",
                "Thresholds must be numbers and magnet position must be a whole number.",
            )
            return
        if not (0.0 <= box_threshold <= 1.0 and 0.0 <= text_threshold <= 1.0):
            messagebox.showerror("Reach and Grab", "Thresholds must be between 0 and 1.")
            return
        if magnet_position < 0:
            messagebox.showerror("Reach and Grab", "Magnet position must be zero or greater.")
            return

        workflow_text = self.reach_and_grab_workflow_id.get().strip()
        event_text = self.reach_and_grab_workflow_event_id.get().strip()
        try:
            workflow_id = int(workflow_text) if workflow_text else None
            workflow_event_id = int(event_text) if event_text else None
        except ValueError:
            messagebox.showerror("Reach and Grab", "Workflow IDs must be whole numbers.")
            return
        if workflow_event_id is not None and workflow_id is None:
            messagebox.showerror(
                "Reach and Grab",
                "Workflow event ID requires a workflow ID.",
            )
            return

        try:
            payload = reach_and_grab_payload(
                sender=self.config_values.sender,
                phrase=target,
                use_model=bool(self.reach_and_grab_use_model.get()),
                model_name=self.reach_and_grab_model_name.get(),
                box_threshold=box_threshold,
                text_threshold=text_threshold,
                magnet_position=magnet_position,
                workflow_id=workflow_id,
                workflow_event_id=workflow_event_id,
            )
        except (TypeError, ValueError) as exc:
            messagebox.showerror("Reach and Grab", str(exc))
            return

        self.reach_and_grab_running = True
        self.reach_and_grab_current_action_id = str(payload["action_id"])
        self.reach_and_grab_request = dict(payload)
        self.reach_and_grab_progress = []
        self.reach_and_grab_photo_path = None
        self.reach_and_grab_action_text.set(
            f"Action ID: {self.reach_and_grab_current_action_id}"
        )
        self.reach_and_grab_status_text.set(
            "Requesting a fresh photo; waiting for firmware and the Vision server…"
        )
        self.reach_and_grab_button.configure(state="disabled")
        self._set_reach_and_grab_result_text(
            "Reach-and-grab started.\n\n"
            + json.dumps(payload, indent=2, sort_keys=True)
            + "\n\nThe GUI will not publish any child motion commands."
        )
        self._run_worker(
            "reach and grab",
            lambda: self.robot.reach_and_grab(payload, CAPTURE_DIR),
            on_success=self._render_reach_and_grab_result,
        )

    def _set_reach_and_grab_result_text(self, text: str) -> None:
        if not hasattr(self, "reach_and_grab_result_box"):
            return
        self.reach_and_grab_result_box.configure(state="normal")
        self.reach_and_grab_result_box.delete("1.0", tk.END)
        self.reach_and_grab_result_box.insert(tk.END, text)
        self.reach_and_grab_result_box.configure(state="disabled")

    def _reach_and_grab_message_summary(self, message: Dict[str, Any]) -> str:
        sender = str(message.get("sender") or "unknown")
        status = str(message.get("status") or "message")
        stage = str(message.get("stage") or "")
        log = str(message.get("log") or "")
        error = str(message.get("error") or "")
        detail = stage or log or error
        if sender == "firmware" and log == "sent":
            detail = "photo sent"
        return f"{sender} · {status}" + (f" · {detail}" if detail else "")

    def _render_reach_and_grab_progress_text(self) -> None:
        lines = [
            f"Action ID: {self.reach_and_grab_current_action_id or '-'}",
            f"Target: {self.reach_and_grab_request.get('phrase', '-')}",
            "",
            "Progress:",
        ]
        if self.reach_and_grab_progress:
            for index, message in enumerate(self.reach_and_grab_progress, start=1):
                lines.append(f"{index}. {self._reach_and_grab_message_summary(message)}")
            lines.extend(
                [
                    "",
                    "Latest message:",
                    json.dumps(self.reach_and_grab_progress[-1], indent=2, sort_keys=True),
                ]
            )
        else:
            lines.append("Waiting for the first matching MQTT message…")
        self._set_reach_and_grab_result_text("\n".join(lines))

    def _render_reach_and_grab_progress(self, message: Dict[str, Any]) -> None:
        action_id = str(message.get("action_id") or "")
        if not action_id or action_id != self.reach_and_grab_current_action_id:
            return
        self.reach_and_grab_progress.append(message)

        sender = str(message.get("sender") or "")
        status = str(message.get("status") or "").lower()
        stage = str(message.get("stage") or "")
        log = str(message.get("log") or "")
        if sender == "firmware":
            if log == "sent":
                self.reach_and_grab_status_text.set(
                    "Firmware sent the photo; waiting for Vision inference and motion planning…"
                )
            else:
                self.reach_and_grab_status_text.set("Firmware is capturing the detection photo…")
        elif status == "in_progress" and stage == "executing_reach_and_grab":
            step_count = message.get("motion_step_count")
            self.reach_and_grab_status_text.set(
                "Vision server is executing reach-and-grab"
                + (f" ({step_count} planned robot steps)…" if step_count is not None else "…")
            )
        elif status == "in_progress":
            self.reach_and_grab_status_text.set(
                str(message.get("log") or "Vision server is processing the detection…")
            )

        if sender == "visual_ai" and status in {"completed", "failed"}:
            self._render_reach_and_grab_terminal(message)
        else:
            self._render_reach_and_grab_progress_text()

    def _render_reach_and_grab_terminal(
        self,
        response: Dict[str, Any],
        photo_path: Optional[Path] = None,
    ) -> None:
        status = str(response.get("status") or "unknown").lower()
        stage = str(response.get("stage") or "")
        grab_status = response.get("grab_status")
        physical_success = (
            status == "completed"
            and stage == "reach_and_grab_completed"
            and grab_status == "completed"
        )

        if physical_success:
            summary = "Completed — the Vision server confirmed the object was grabbed."
        elif status == "completed" and stage == "detection_only":
            summary = "Detection completed, but automatic robot movement is disabled."
        elif status == "failed":
            summary, failure_explanation = explain_reach_and_grab_failure(response)
        else:
            summary = "Completed response received, but a successful physical grab was not confirmed."

        if status != "failed":
            failure_explanation = []

        execution_message = next(
            (
                message
                for message in reversed(self.reach_and_grab_progress)
                if message.get("sender") == "visual_ai"
                and message.get("stage") == "executing_reach_and_grab"
            ),
            {},
        )
        raw_x = response.get("raw_x", execution_message.get("raw_x", "-"))
        raw_y = response.get("raw_y", execution_message.get("raw_y", "-"))

        self.reach_and_grab_running = False
        self.reach_and_grab_button.configure(state="normal")
        self.reach_and_grab_status_text.set(summary)
        self.status_text.set("Done: reach and grab" if physical_success else "Reach and grab finished")

        details = [
            summary,
            "",
            f"Action ID: {response.get('action_id', '-')}",
            f"Target: {response.get('phrase', self.reach_and_grab_request.get('phrase', '-'))}",
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
            details.append(
                f"Planned robot steps: {execution_message.get('motion_step_count', '-')}"
            )
        if response.get("warning"):
            details.append(f"Warning: {response['warning']}")
        if response.get("error"):
            details.append(f"Error: {response['error']}")
        details.extend(failure_explanation)
        if response.get("failed_step") is not None:
            details.append(f"Failed step: {response['failed_step']}")
        if response.get("failed_action"):
            details.append(f"Failed action: {response['failed_action']}")
        if photo_path or self.reach_and_grab_photo_path:
            details.append(f"Photo: {photo_path or self.reach_and_grab_photo_path}")
        details.extend(["", "Progress:"])
        for index, message in enumerate(self.reach_and_grab_progress, start=1):
            details.append(f"{index}. {self._reach_and_grab_message_summary(message)}")
        details.extend(["", "Terminal Visual AI message:", json.dumps(response, indent=2, sort_keys=True)])
        self._set_reach_and_grab_result_text("\n".join(details))

        self.session["reach_and_grab"] = {
            "request": self.reach_and_grab_request,
            "response": response,
            "photo": str(photo_path or self.reach_and_grab_photo_path or ""),
            "progress": self.reach_and_grab_progress,
        }
        self.last_result_text.set(
            "Reach-and-grab completed" if physical_success else "Reach-and-grab finished"
        )

    def _render_reach_and_grab_result(self, result: ReachAndGrabResult) -> None:
        self.reach_and_grab_request = dict(result.request)
        self.reach_and_grab_progress = list(result.progress)
        if result.photo_path:
            self.reach_and_grab_photo_path = result.photo_path
        self._render_reach_and_grab_terminal(result.response, result.photo_path)

    def save_perch_angles(self) -> None:
        def work() -> None:
            self.robot.request(save_perch_payload(self.config_values.sender, "elbow", self.perch_angle_vars["ELBOW"].get()))
            self.robot.request(save_perch_payload(self.config_values.sender, "wrist", self.perch_angle_vars["WRIST"].get()))
            self.robot.request(save_perch_payload(self.config_values.sender, "twist", self.perch_angle_vars["TWIST"].get()))
        self.session["perch"] = {name: var.get() for name, var in self.perch_angle_vars.items()}
        self._run_worker("save perch values", work, on_success=lambda _: self.refresh_status())

    def save_perch_distances(self) -> None:
        def work() -> None:
            for kind, var in self.perch_dist_vars.items():
                self.robot.request(save_perch_payload(self.config_values.sender, kind, var.get()))
        self.session["perch_distances"] = {kind: var.get() for kind, var in self.perch_dist_vars.items()}
        self._run_worker("save perch distances", work, on_success=lambda _: self.refresh_status())

    def use_controller_for_perch(self) -> None:
        for name in ("ELBOW", "WRIST", "TWIST"):
            self.perch_angle_vars[name].set(self.controller_angles[name].get())

    def use_controller_for_ik(self, plane: str, kind: str) -> None:
        row = self.ik_rows[plane][kind]
        row["elbow"].set(self.controller_angles["ELBOW"].get())
        row["wrist"].set(self.controller_angles["WRIST"].get())
        row["twist"].set(self.controller_angles["TWIST"].get())

    def send_ik_control(self) -> None:
        try:
            distance_y = int(self.ik_control_y.get())
            height_z = int(self.ik_control_z.get())
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
            response = self.robot.request(
                ik_payload(self.config_values.sender, float(distance_y), float(height_z))
            )
            if str(response.get("status") or "").lower() != "completed":
                raise RuntimeError(
                    f"Robot rejected IK target Y={distance_y} mm, Z={height_z} mm. "
                    "Check that the requested point is inside the calibrated workspace."
                )
            return response

        self._run_worker(
            f"IK Y={distance_y} mm Z={height_z} mm",
            work,
            on_success=lambda _response: self.last_result_text.set(
                f"IK completed: Y={distance_y} mm, Z={height_z} mm"
            ),
        )

    def move_ik_row(self, plane: str, kind: str) -> None:
        row = self.ik_rows[plane][kind]
        self.controller_angles["ELBOW"].set(int(row["elbow"].get()))
        self.controller_angles["WRIST"].set(int(row["wrist"].get()))
        self.controller_angles["TWIST"].set(int(row["twist"].get()))
        self.move_all_servos()

    def save_ik_row(self, plane: str, kind: str) -> None:
        row = self.ik_rows[plane][kind]
        payload = save_hover_payload(
            self.config_values.sender,
            plane,
            kind,
            float(row["distance"].get()),
            int(row["elbow"].get()),
            int(row["wrist"].get()),
            int(row["twist"].get()),
        )
        self.session.setdefault("ik_saved", {}).setdefault(plane, {})[kind] = {
            "distance": row["distance"].get(),
            "ELBOW": row["elbow"].get(),
            "WRIST": row["wrist"].get(),
            "TWIST": row["twist"].get(),
        }
        self._run_worker(
            f"save {plane} {kind}",
            lambda: self.robot.request(payload),
            on_success=lambda _: self._after_ik_saved(row),
        )

    def _after_ik_saved(self, row: Dict[str, tk.Variable]) -> None:
        row["result"].set("Saved")
        self.refresh_status()

    def run_ik_validation(self, plane: str, kind: str) -> None:
        row = self.ik_rows[plane][kind]
        distance = float(row["distance"].get())
        self._run_worker(
            f"run IK {kind} z=25",
            lambda: self.robot.request(ik_payload(self.config_values.sender, distance, 25.0)),
            on_success=lambda _: row["result"].set("Ran IK z=25"),
        )

    def mark_ik_validation(self, plane: str, kind: str, passed: bool) -> None:
        row = self.ik_rows[plane][kind]
        result = "PASS" if passed else "FAIL"
        row["result"].set(result)
        self.session.setdefault("ik_validation", {}).setdefault(plane, {})[kind] = {
            "passed": passed,
            "distance": row["distance"].get(),
            "photo": str(self.last_photo_path) if self.last_photo_path else "",
        }

    def stencil_start(self) -> None:
        self._run_stencil_command("start stencil session", "START")

    def stencil_run_point(self) -> None:
        self._run_stencil_command("run stencil point", "RUN_POINT")

    def stencil_status_request(self) -> None:
        self._run_stencil_command("stencil status", "STATUS")

    def stencil_cancel(self) -> None:
        self._run_stencil_command("cancel stencil session", "CANCEL")

    def stencil_clear(self) -> None:
        if not messagebox.askyesno(
            "Clear Saved Stencil",
            "Clear saved stencil offsets from the robot? This removes st_map, rot_off_deg, and ik_off_mm.",
        ):
            return
        self._run_stencil_command("clear saved stencil", "CLEAR", refresh_after=True)

    def stencil_adjust(self) -> None:
        self._run_stencil_command(
            "adjust stencil point",
            "ADJUST",
            rotation=float(self.stencil_rotation_nudge.get()),
            distance=float(self.stencil_distance_nudge.get()),
        )

    def stencil_adjust_previous_retry(self) -> None:
        self._run_stencil_command(
            "adjust and retry previous stencil point",
            "ADJUST_PREVIOUS",
            rotation=float(self.stencil_rotation_nudge.get()),
            distance=float(self.stencil_distance_nudge.get()),
        )

    def _run_stencil_command(
        self,
        label: str,
        command: str,
        rotation: Optional[float] = None,
        distance: Optional[float] = None,
        refresh_after: bool = False,
    ) -> None:
        payload = stencil_payload(self.config_values.sender, command, rotation=rotation, distance=distance)

        def after(response: Dict[str, Any]) -> None:
            self._render_stencil_response(response)
            stencil = response.get("stencil_calibration") if isinstance(response, dict) else None
            phase = stencil.get("phase") if isinstance(stencil, dict) else ""
            if refresh_after or phase in {"complete", "cleared"}:
                self.refresh_status()

        self._run_worker(label, lambda: self.robot.request(payload), on_success=after)

    def _render_stencil_response(self, response: Dict[str, Any]) -> None:
        if not hasattr(self, "stencil_status_box") or not hasattr(self, "stencil_points_box"):
            return

        stencil = response.get("stencil_calibration") if isinstance(response, dict) else None
        if not isinstance(stencil, dict):
            stencil = self.stencil_status
        else:
            self.stencil_status = stencil

        self.stencil_status_box.delete("1.0", tk.END)
        self.stencil_points_box.delete("1.0", tk.END)

        if not stencil:
            self.stencil_status_box.insert(tk.END, "No stencil status yet. Click Status or Start Session.")
            self.stencil_points_box.insert(tk.END, "No point progress yet.")
            return

        fields = [
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
        for label, key in fields:
            value = stencil.get(key)
            if value is not None and value != "":
                self.stencil_status_box.insert(tk.END, f"{label}: {self._format_stencil_value(value)}\n")

        points = stencil.get("points")
        if not isinstance(points, list):
            self.stencil_points_box.insert(tk.END, "No point progress in response.")
            return

        self.stencil_points_box.insert(tk.END, "idx  point             z  angle  dist  offset  done  grab  rotNudge  distNudge  attempts\n")
        self.stencil_points_box.insert(tk.END, "---  ----------------  --  -----  ----  ------  ----  ----  --------  ---------  --------\n")
        for idx, point in enumerate(points):
            if not isinstance(point, dict):
                continue
            angle = point.get("angleDegrees", point.get("angle"))
            distance = point.get("distanceMm", point.get("distance"))
            z_height = point.get("zHeightMm", point.get("z"))
            self.stencil_points_box.insert(
                tk.END,
                (
                    f"{idx:>3}  "
                    f"{str(point.get('id', '-')):<16}  "
                    f"{self._format_stencil_number(z_height):>2}  "
                    f"{self._format_stencil_number(angle):>5}  "
                    f"{self._format_stencil_number(distance):>4}  "
                    f"{self._format_bool(point.get('offsetContributor')):<6}  "
                    f"{self._format_bool(point.get('completed')):<4}  "
                    f"{self._format_bool(point.get('grabbed')):<4}  "
                    f"{self._format_stencil_number(point.get('rotationNudgeDegrees')):>8}  "
                    f"{self._format_stencil_number(point.get('distanceNudgeMm')):>9}  "
                    f"{self._format_stencil_value(point.get('attempts')):>8}\n"
                ),
            )

    def _format_stencil_value(self, value: Any) -> str:
        if isinstance(value, bool):
            return self._format_bool(value)
        if isinstance(value, (int, float)):
            return self._format_stencil_number(value)
        return str(value)

    def _format_stencil_number(self, value: Any) -> str:
        if value is None:
            return "-"
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if number.is_integer():
            return str(int(number))
        return f"{number:.2f}"

    def save_session_summary(self) -> None:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        self.session["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.session["last_photo"] = str(self.last_photo_path) if self.last_photo_path else ""
        self.session["calibrationvalues"] = self.robot.state.calibrationvalues
        path = SESSION_DIR / f"session_{time.strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(self.session, indent=2, sort_keys=True))
        self._info(f"Session summary saved to {path}")

    def _display_photo(self, path: Path) -> None:
        self.last_photo_path = Path(path)
        self.session["captures"].append(str(path))
        if Image is None or ImageTk is None:
            for label in self.photo_labels:
                label.configure(text=f"Photo saved: {path}\nInstall Pillow to preview images:\npython3 -m pip install Pillow", image="")
            self.controller_photo_label.configure(
                text=f"Photo saved: {path}\nInstall Pillow to preview images:\npython3 -m pip install Pillow",
                image="",
            )
            return
        image = Image.open(path)
        image.thumbnail((360, 300))
        self.photo_image = ImageTk.PhotoImage(image)
        for label in self.photo_labels:
            label.configure(image=self.photo_image, text="")
        self.controller_photo_render_width = 0
        self._schedule_controller_photo_resize()

    def _schedule_controller_photo_resize(self) -> None:
        if not self.last_photo_path or not hasattr(self, "controller_photo_label"):
            return
        if self.controller_photo_resize_job is not None:
            self.after_cancel(self.controller_photo_resize_job)
        self.controller_photo_resize_job = self.after(50, self._render_controller_photo)

    def _render_controller_photo(self) -> None:
        self.controller_photo_resize_job = None
        if not self.last_photo_path or Image is None or ImageTk is None:
            return

        panel_width = self.controller_photo_panel.winfo_width()
        if panel_width <= 1:
            panel_width = self.controller_canvas.winfo_width()
        available_width = max(1, panel_width - max(16, round(20 * self.ui_zoom)))
        if available_width == self.controller_photo_render_width and self.controller_photo_image is not None:
            return

        with Image.open(self.last_photo_path) as source:
            source.load()
            output_size = fit_image_to_width(source.width, source.height, available_width)
            if output_size == source.size:
                rendered = source.copy()
            else:
                resampling = getattr(Image, "Resampling", Image)
                rendered = source.resize(output_size, resampling.LANCZOS)

        self.controller_photo_image = ImageTk.PhotoImage(rendered)
        self.controller_photo_label.configure(image=self.controller_photo_image, text="")
        self.controller_photo_render_width = available_width

    def _display_visual_calibration_photo(self, path: Path) -> None:
        path = Path(path)
        self.last_photo_path = path
        if str(path) not in self.session["captures"]:
            self.session["captures"].append(str(path))
        self.visual_calibration_status_text.set(
            "Photo received; waiting for the Vision server to build the calibration grid…"
        )
        if Image is None or ImageTk is None:
            self.visual_calibration_photo_label.configure(
                text=(
                    f"Photo saved: {path}\n"
                    "Install Pillow to preview images:\n"
                    "python3 -m pip install Pillow"
                ),
                image="",
            )
            return
        image = Image.open(path)
        image.thumbnail((460, 300))
        self.visual_calibration_photo_image = ImageTk.PhotoImage(image)
        self.visual_calibration_photo_label.configure(
            image=self.visual_calibration_photo_image,
            text="",
        )

    def _display_reach_and_grab_photo(self, path: Path) -> None:
        path = Path(path)
        self.last_photo_path = path
        self.reach_and_grab_photo_path = path
        if str(path) not in self.session["captures"]:
            self.session["captures"].append(str(path))
        if self.reach_and_grab_running:
            self.reach_and_grab_status_text.set(
                "Detection photo received; waiting for Vision inference and robot execution…"
            )
        if Image is None or ImageTk is None:
            self.reach_and_grab_photo_label.configure(
                text=(
                    f"Photo saved: {path}\n"
                    "Install Pillow to preview images:\n"
                    "python3 -m pip install Pillow"
                ),
                image="",
            )
            return
        image = Image.open(path)
        image.thumbnail((460, 300))
        self.reach_and_grab_photo_image = ImageTk.PhotoImage(image)
        self.reach_and_grab_photo_label.configure(
            image=self.reach_and_grab_photo_image,
            text="",
        )

    def _run_worker(self, label: str, work: Callable[[], Any], on_success: Optional[Callable[[Any], None]] = None) -> None:
        self.status_text.set(f"Working: {label}")

        def runner() -> None:
            try:
                result = work()
                self.events.put(("worker_success", (label, result, on_success)))
            except Exception as exc:
                traceback.print_exc(file=sys.stderr)
                self.events.put(("worker_error", (label, str(exc))))

        threading.Thread(target=runner, daemon=True).start()

    def _process_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            self._append_log(kind, payload)
            if kind == "worker_success":
                label, result, callback = payload
                self.status_text.set(f"Done: {label}")
                self.last_result_text.set(f"Last result: {label} completed")
                if callback:
                    callback(result)
            elif kind == "worker_error":
                label, error = payload
                print(f"[CalibrationWizard] {label} failed: {error}", file=sys.stderr)
                self.status_text.set(f"Error: {label}")
                self.last_result_text.set(error)
                if label == "visual calibration":
                    self.visual_calibration_status_text.set(f"Failed — {error}")
                    self._set_visual_calibration_result_text(f"Error: {error}")
                elif label == "reach and grab":
                    self.reach_and_grab_running = False
                    self.reach_and_grab_button.configure(state="normal")
                    if "Do not automatically resend" in error:
                        self.reach_and_grab_status_text.set(
                            "Timed out with an uncertain robot state. Do not automatically resend; "
                            "the robot may already have moved. Late matching Vision results will still be shown."
                        )
                    else:
                        self.reach_and_grab_status_text.set(f"Failed — {error}")
                    self._set_reach_and_grab_result_text(
                        f"Reach-and-grab did not produce a terminal result in this wait.\n\n{error}\n\n"
                        "No retry was sent. Check the shared-topic activity log before deciding what to do next."
                    )
                messagebox.showerror("Calibration Wizard", error)
            elif kind == "error":
                print(f"[CalibrationWizard] {payload}", file=sys.stderr)
                self.status_text.set(str(payload))
                self.last_result_text.set(str(payload))
            elif kind == "state":
                self._render_state()
            elif kind == "sent":
                self.last_result_text.set("Sent: " + json.dumps(payload, separators=(",", ":")))
            elif kind == "photo_saved":
                self.last_result_text.set(f"Photo saved: {payload}")
            elif kind == "visual_calibration_photo_saved":
                self._display_visual_calibration_photo(Path(payload))
            elif kind == "visual_calibration_result":
                status = str(payload.get("status") or "result")
                self.last_result_text.set(f"Visual AI calibration: {status}")
            elif kind == "reach_and_grab_photo_saved":
                self._display_reach_and_grab_photo(Path(payload))
            elif kind == "reach_and_grab_progress":
                self._render_reach_and_grab_progress(payload)
        self.after(150, self._process_events)

    def _visible_topic_log_lines(self) -> list[str]:
        show_heartbeats = self.show_heartbeats.get()
        return [line for line, heartbeat in self.topic_log_entries if show_heartbeats or not heartbeat]

    def _render_topic_log(self, follow: bool = True) -> None:
        lines = self._visible_topic_log_lines()
        self.topic_log.configure(state="normal")
        self.topic_log.delete("1.0", tk.END)
        self.topic_log.insert(tk.END, "\n".join(lines))
        if lines:
            self.topic_log.insert(tk.END, "\n")
        if follow:
            self.topic_log.see(tk.END)
        self.topic_log.configure(state="disabled")

    def _append_log(self, kind: str, payload: Any) -> None:
        line_body = format_topic_log_event(kind, payload)
        if not line_body:
            return
        should_follow = self.topic_log.yview()[1] >= 0.98
        line = f"[{time.strftime('%H:%M:%S')}] {line_body}"
        self.topic_log_entries.append((line, is_heartbeat_event(kind, payload)))
        if len(self.topic_log_entries) > MAX_TOPIC_LOG_LINES:
            self.topic_log_entries = self.topic_log_entries[-MAX_TOPIC_LOG_LINES:]
        self._render_topic_log(follow=should_follow)

    def _clear_log(self) -> None:
        self.topic_log_entries.clear()
        self._render_topic_log(follow=False)

    def _copy_to_system_clipboard(self, text: str) -> bool:
        """Hand the text to a clipboard manager so it survives this app exiting.

        Tk owns its selection only while the process lives and its event loop is
        pumping, so a copy made during a long worker call pastes as nothing.
        Returns False when no helper is available and Tk must be used instead.
        """
        for command in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            if not shutil.which(command[0]):
                continue
            try:
                subprocess.run(command, input=text, text=True, timeout=5, check=True)
                return True
            except (OSError, subprocess.SubprocessError):
                continue
        return False

    def _copy_log(self) -> None:
        lines = self._visible_topic_log_lines()
        if not lines:
            self.last_result_text.set("Nothing to copy")
            return
        text = "\n".join(lines)
        if self._copy_to_system_clipboard(text):
            self.last_result_text.set(f"Copied {len(lines)} log lines to clipboard")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()  # Serve the selection now instead of at the next idle moment.
        self.last_result_text.set(f"Copied {len(lines)} log lines (keep this app open to paste)")

    def _tick_connection_status(self) -> None:
        age = self.robot.state.heartbeat_age()
        if age is None:
            self.robot_text.set("Robot offline")
            self.heartbeat_text.set("No heartbeat yet")
            self.system_status_vars["heartbeat"].set("—")
        else:
            online = age < 12
            self.robot.state.robot_online = online
            self.robot_text.set("Robot online" if online else "Robot stale")
            self.heartbeat_text.set(f"Heartbeat {age:.1f}s ago")
            self.system_status_vars["heartbeat"].set(f"{age:.1f} seconds ago")
        self.after(1000, self._tick_connection_status)

    def _render_state(self) -> None:
        state = self.robot.state
        self.status_text.set("MQTT connected" if state.broker_connected else "MQTT disconnected")
        values = state.calibrationvalues
        hb = state.last_heartbeat
        ready = state.last_ready
        self._update_observed_telemetry(values, hb, state.last_response)
        self._sync_ik_rows_from_calibrationvalues(values)
        self._sync_perch_from_calibrationvalues(values)
        self._render_calibration_status(values, hb, ready, state.last_error)

    def _render_calibration_status(
        self,
        values: Dict[str, Any],
        heartbeat: Dict[str, Any],
        ready: Dict[str, Any],
        last_error: str,
    ) -> None:
        self.status_summary_vars["overall"].set(
            "Ready" if values.get("initial_calibration_ready") else "Needs work"
        )
        self.status_summary_vars["base"].set(
            "Ready" if values.get("base_rotation_ready") else "Not calibrated"
        )
        self.status_summary_vars["perch"].set(
            "Saved" if values.get("perch_configured") else "Using defaults"
        )
        if values.get("ik_hover_calibrated"):
            ik_status = "Table + upper ready" if values.get("ik_z50_calibrated") else "Table ready"
        else:
            ik_status = "Not calibrated"
        self.status_summary_vars["ik"].set(ik_status)
        self.status_summary_vars["stencil"].set(
            "Ready" if values.get("stencil_calibrated") else "Not calibrated"
        )

        firmware_version = heartbeat.get("firmware_version") or ready.get("firmware_version")
        ota_state = heartbeat.get("ota_state") or ready.get("ota_state")
        firmware_text = str(firmware_version or "—")
        if ota_state:
            firmware_text += f" · {ota_state}"
        self.system_status_vars["firmware"].set(firmware_text)
        age = self.robot.state.heartbeat_age()
        self.system_status_vars["heartbeat"].set("—" if age is None else f"{age:.1f} seconds ago")
        self.system_status_vars["reset"].set(str(ready.get("last_reset_reason") or "—"))
        self.system_status_vars["error"].set(last_error or "None")

        for item_id in self.status_tree.get_children():
            self.status_tree.delete(item_id)

        groups: Dict[str, str] = {}
        state_labels = {
            "SAVED": "✓ Saved",
            "DEFAULT": "○ Default",
            "MISSING": "! Missing",
            "OPTIONAL": "— Optional",
        }
        state_tags = {
            "SAVED": "saved",
            "DEFAULT": "default",
            "MISSING": "missing",
            "OPTIONAL": "optional",
        }
        for row in build_calibration_status_rows(values):
            group_name = row["group"]
            if group_name not in groups:
                groups[group_name] = self.status_tree.insert(
                    "",
                    tk.END,
                    text=group_name,
                    values=("", "", "", ""),
                    tags=("group",),
                    open=True,
                )
            state = row["state"]
            self.status_tree.insert(
                groups[group_name],
                tk.END,
                text=row["label"],
                values=(
                    state_labels[state],
                    row["key"],
                    row["value"],
                    row["source"],
                ),
                tags=(state_tags[state],),
            )

    def _sync_perch_from_calibrationvalues(self, values: Dict[str, Any]) -> None:
        if not values or self._is_user_editing():
            return
        effective = values.get("perch_effective")
        if not isinstance(effective, dict):
            return
        signature = json.dumps(effective, sort_keys=True, default=str)
        if signature == self._last_perch_sync_signature:
            return
        for name in ("ELBOW", "WRIST", "TWIST"):
            self._set_var_if_present(self.perch_angle_vars[name], effective.get(name))
        if hasattr(self, "perch_dist_vars"):
            for kind, key in (("min", "MIN"), ("mid", "MID"), ("max", "MAX")):
                self._set_var_if_present(self.perch_dist_vars[kind], effective.get(key))
        self._last_perch_sync_signature = signature

    def _sync_ik_rows_from_calibrationvalues(self, values: Dict[str, Any]) -> None:
        if not hasattr(self, "ik_rows") or not values:
            return

        key_map = {
            ("z0", "min"): "hover_over_min",
            ("z0", "mid"): "hover_over_mid",
            ("z0", "max"): "hover_over_max",
            ("z50", "min"): "hover_min_120",
            ("z50", "mid"): "hover_mid_120",
            ("z50", "max"): "hover_max_120",
        }
        signature_payload = {key: values.get(key) for key in key_map.values()}
        signature = json.dumps(signature_payload, sort_keys=True, default=str)
        if signature == self._last_ik_sync_signature:
            return
        if self._is_user_editing():
            return

        for (plane, kind), firmware_key in key_map.items():
            point = values.get(firmware_key)
            if not isinstance(point, dict):
                continue
            row = self.ik_rows.get(plane, {}).get(kind)
            if not row:
                continue
            self._set_var_if_present(row["distance"], point.get("DISTANCE"))
            self._set_var_if_present(row["elbow"], point.get("ELBOW"))
            self._set_var_if_present(row["wrist"], point.get("WRIST"))
            self._set_var_if_present(row["twist"], point.get("TWIST"))
            row["result"].set("Saved in firmware")

        self._last_ik_sync_signature = signature

    def _set_var_if_present(self, var: tk.Variable, value: Any) -> None:
        if value is None:
            return
        try:
            if isinstance(var, tk.IntVar):
                var.set(int(round(float(value))))
            elif isinstance(var, tk.DoubleVar):
                var.set(float(value))
            else:
                var.set(value)
        except (TypeError, ValueError, tk.TclError):
            pass

    def _update_observed_telemetry(self, values: Dict[str, Any], hb: Dict[str, Any], last_response: Dict[str, Any]) -> None:
        for source_key, target_key in (
            ("ELBOW_ANGLE", "ELBOW"),
            ("WRIST_ANGLE", "WRIST"),
            ("TWIST_ANGLE", "TWIST"),
            ("GRIPPER_ANGLE", "GRIPPER"),
        ):
            value = hb.get(source_key, values.get(source_key))
            formatted = self._format_observed_value(value)
            self.observed_angles[target_key].set(formatted if formatted == "-" else f"{formatted} °")

        base_status = last_response.get("base_rotation")
        if not isinstance(base_status, dict):
            base_status = {}
        angle = base_status.get("baseAngleDegrees")
        counts = base_status.get("basePositionCounts", values.get("base_rotation_lastCounts"))
        calibrated = base_status.get("calibrated", values.get("base_rotation_calibrated"))
        trusted = base_status.get("positionTrusted", values.get("base_rotation_lastValid"))
        if angle is not None:
            self.observed_base_text.set(
                f"{self._format_observed_value(angle)}° · {self._format_observed_value(counts)} counts · "
                f"Cal {self._format_bool(calibrated)} · Trusted {self._format_bool(trusted)}"
            )
        else:
            self.observed_base_text.set(
                f"{self._format_observed_value(counts)} counts · "
                f"Cal {self._format_bool(calibrated)} · Trusted {self._format_bool(trusted)}"
            )

    def _format_observed_value(self, value: Any) -> str:
        if value is None:
            return "-"
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if number.is_integer():
            return str(int(number))
        return f"{number:.2f}"

    def _format_bool(self, value: Any) -> str:
        if value is None:
            return "-"
        return "yes" if bool(value) else "no"

    def _info(self, message: str) -> None:
        self.last_result_text.set(message)
        messagebox.showinfo("Calibration Wizard", message)


def main() -> None:
    app = CalibrationWizard()
    app.mainloop()


if __name__ == "__main__":
    main()
