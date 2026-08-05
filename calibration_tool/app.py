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
        base_angle_payload,
        base_degrees_payload,
        base_profile_payload,
        base_status_payload,
        base_steps_payload,
        gripper_payload,
        ik_payload,
        perch_payload,
        save_hover_payload,
        save_perch_payload,
        servo_payload,
    )
    from .controllers.base_perch_controller import BasePerchController
    from .controllers.ik_controller import IkController
    from .controllers.reach_and_grab_controller import ReachAndGrabController
    from .controllers.status_controller import StatusController
    from .controllers.stencil_controller import StencilController
    from .controllers.visual_calibration_controller import VisualCalibrationController
    from .models.reach_and_grab_state import explain_reach_and_grab_failure as _explain_reach_and_grab_failure
    from .models.status_state import build_calibration_status_rows as _build_calibration_status_rows
    from .views.base_perch_view import BasePerchView
    from .views.ik_view import IkView
    from .views.reach_and_grab_view import ReachAndGrabView
    from .views.status_view import StatusView
    from .views.stencil_view import StencilView
    from .views.visual_calibration_view import VisualCalibrationView
except ImportError:  # pragma: no cover - direct script execution
    from config import DEFAULT_CA_CERT, ENV_PATH, WizardConfig, load_config, save_config
    from mqtt_robot import (
        MqttRobot,
        base_angle_payload,
        base_degrees_payload,
        base_profile_payload,
        base_status_payload,
        base_steps_payload,
        gripper_payload,
        ik_payload,
        perch_payload,
        save_hover_payload,
        save_perch_payload,
        servo_payload,
    )
    from controllers.base_perch_controller import BasePerchController
    from controllers.ik_controller import IkController
    from controllers.reach_and_grab_controller import ReachAndGrabController
    from controllers.status_controller import StatusController
    from controllers.stencil_controller import StencilController
    from controllers.visual_calibration_controller import VisualCalibrationController
    from models.reach_and_grab_state import explain_reach_and_grab_failure as _explain_reach_and_grab_failure
    from models.status_state import build_calibration_status_rows as _build_calibration_status_rows
    from views.base_perch_view import BasePerchView
    from views.ik_view import IkView
    from views.reach_and_grab_view import ReachAndGrabView
    from views.status_view import StatusView
    from views.stencil_view import StencilView
    from views.visual_calibration_view import VisualCalibrationView


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


# Re-exported for tests.py and any other caller that imported this from here
# before it moved to models/status_state.py alongside the rest of the Status
# tab's logic.
build_calibration_status_rows = _build_calibration_status_rows


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


# Re-exported for tests.py and any other caller that imported this from here
# before it moved to models/reach_and_grab_state.py alongside the rest of the
# Reach and Grab logic.
explain_reach_and_grab_failure = _explain_reach_and_grab_failure


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
        self.base_rotation_value = tk.IntVar(value=10)
        self.base_direction = tk.StringVar(value="LEFT")
        self.base_speed = tk.StringVar(value="slow")
        self.observed_base_text = tk.StringVar(value="Base: no status yet")
        self.live_mode = tk.BooleanVar(value=False)
        self.user_editing_until = 0.0
        self.last_photo_path: Optional[Path] = None
        self.photo_image: Any = None
        self.photo_labels: list[ttk.Label] = []
        self.controller_photo_image: Any = None
        self.controller_photo_resize_job: Optional[str] = None
        self.controller_photo_render_width = 0
        # (line, is_heartbeat) so the heartbeat filter can re-render history
        # rather than only affecting messages that arrive after it is toggled.
        self.topic_log_entries: list[tuple[str, bool]] = []
        self.show_heartbeats = tk.BooleanVar(value=False)
        self.show_controller = tk.BooleanVar(value=True)
        self.show_activity_log = tk.BooleanVar(value=True)
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
            return

        self.update_idletasks()
        self._restore_geometry = self.geometry()
        max_width, max_height = self.maxsize()
        self.geometry(f"{max_width}x{max_height}+0+0")
        self._manual_maximized = True

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
        ttk.Button(window_tools, text="Refresh", style="Accent.TButton", command=self.refresh_status).pack(side="left")

        self.main_pane = ttk.PanedWindow(self, orient="vertical")
        self.main_pane.grid(row=1, column=0, sticky="nsew")

        workspace = ttk.Frame(self.main_pane, padding=(14, 0, 14, 8))
        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(0, weight=1)
        self.main_pane.add(workspace, weight=6)

        self.connect_view = ttk.Frame(workspace, padding=12)
        self.connect_view.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.setup_tab = self.connect_view

        self.notebook = ttk.Notebook(workspace)
        self.notebook.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.status_tab = ttk.Frame(self.notebook, padding=12)
        self.base_perch_tab = ttk.Frame(self.notebook, padding=12)
        self.ik_tab = ttk.Frame(self.notebook, padding=12)
        self.visual_calibration_tab = ttk.Frame(self.notebook, padding=12)
        self.reach_and_grab_tab = ttk.Frame(self.notebook, padding=12)
        self.stencil_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.status_tab, text="Status")
        self.notebook.add(self.base_perch_tab, text="Base + Perch")
        self.notebook.add(self.ik_tab, text="IK")
        self.notebook.add(self.visual_calibration_tab, text="Visual Calibration")
        self.notebook.add(self.reach_and_grab_tab, text="Reach and Grab")
        self.notebook.add(self.stencil_tab, text="Stencil")

        self._calibration_tab_ids = {
            "Status": self.status_tab,
            "Base + Perch": self.base_perch_tab,
            "IK": self.ik_tab,
            "Visual Calibration": self.visual_calibration_tab,
            "Reach and Grab": self.reach_and_grab_tab,
            "Stencil": self.stencil_tab,
        }
        self._build_menu_bar()
        self._show_connect_view()

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

    def _build_menu_bar(self) -> None:
        menu_bar = tk.Menu(self)

        connect_menu = tk.Menu(menu_bar, tearoff=False)
        connect_menu.add_command(label="Broker Settings", command=self._show_connect_view)
        menu_bar.add_cascade(label="Connect", menu=connect_menu)

        calibration_menu = tk.Menu(menu_bar, tearoff=False)
        for label in self._calibration_tab_ids:
            calibration_menu.add_command(
                label=label,
                command=lambda label=label: self._show_calibration_tab(label),
            )
        menu_bar.add_cascade(label="Calibration", menu=calibration_menu)

        # Workflow and Models have no content yet; placeholders keep the menu
        # bar shape ready for whatever lands in them.
        workflow_menu = tk.Menu(menu_bar, tearoff=False)
        workflow_menu.add_command(label="(nothing here yet)", state="disabled")
        menu_bar.add_cascade(label="Workflow", menu=workflow_menu)

        models_menu = tk.Menu(menu_bar, tearoff=False)
        models_menu.add_command(label="(nothing here yet)", state="disabled")
        menu_bar.add_cascade(label="Models", menu=models_menu)

        tools_menu = tk.Menu(menu_bar, tearoff=False)
        tools_menu.add_checkbutton(
            label="Controller",
            variable=self.show_controller,
            command=self._apply_controller_visibility,
        )
        tools_menu.add_checkbutton(
            label="Activity Log",
            variable=self.show_activity_log,
            command=self._apply_activity_log_visibility,
        )
        menu_bar.add_cascade(label="Tools", menu=tools_menu)

        view_menu = tk.Menu(menu_bar, tearoff=False)
        view_menu.add_command(label="Zoom In", command=self._zoom_in, accelerator="Ctrl++")
        view_menu.add_command(label="Zoom Out", command=self._zoom_out, accelerator="Ctrl+-")
        view_menu.add_command(label="Reset Zoom", command=self._reset_zoom, accelerator="Ctrl+0")
        view_menu.add_separator()
        # Label reflects current window state, so it must be recomputed each
        # time the menu opens rather than fixed at menu-build time.
        view_menu.add_command(label="Maximize", command=self._toggle_maximize)
        maximize_index = view_menu.index("end")
        view_menu.configure(
            postcommand=lambda: view_menu.entryconfigure(
                maximize_index,
                label="Restore" if self._manual_maximized else "Maximize",
            )
        )
        menu_bar.add_cascade(label="View", menu=view_menu)

        self.configure(menu=menu_bar)

    def _show_connect_view(self) -> None:
        self.notebook.grid_remove()
        self.connect_view.grid()

    def _show_calibration_tab(self, label: str) -> None:
        self.connect_view.grid_remove()
        self.notebook.grid()
        self.notebook.select(self._calibration_tab_ids[label])

    def _apply_controller_visibility(self) -> None:
        if self.show_controller.get():
            self.controller_shell.grid()
        else:
            self.controller_shell.grid_remove()

    def _apply_activity_log_visibility(self) -> None:
        if self.show_activity_log.get():
            if str(self.activity_log_pane) not in self.main_pane.panes():
                self.main_pane.add(self.activity_log_pane, weight=1)
        else:
            if str(self.activity_log_pane) in self.main_pane.panes():
                self.main_pane.forget(self.activity_log_pane)

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
        self.activity_log_pane = log_frame
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
        self.status_tab.rowconfigure(0, weight=1)

        status_view = StatusView(
            self.status_tab,
            page_title_font=self.ui_fonts["page_title"],
            muted_color=MUTED,
            ink_color=INK,
            tiny_bold_font=self.ui_fonts["tiny_bold"],
            body_bold_font=self.ui_fonts["body_bold"],
            good_color=GOOD,
            warn_color=WARN,
            bad_color=BAD,
            status_summary_vars=self.status_summary_vars,
            system_status_vars=self.system_status_vars,
        )
        status_view.grid(row=0, column=0, sticky="nsew")
        self.status_tree = status_view.status_tree

        self.status_controller = StatusController(
            status_view,
            connect_mqtt=self.connect_mqtt,
            save_session_summary=self.save_session_summary,
            refresh_status=self.refresh_status,
            heartbeat_age=self.robot.state.heartbeat_age,
        )

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
        self.base_perch_tab.rowconfigure(0, weight=1)

        base_perch_view = BasePerchView(
            self.base_perch_tab,
            bind_edit_guard=self._bind_edit_guard,
            page_title_font=self.ui_fonts["page_title"],
            muted_color=MUTED,
            observed_base_text=self.observed_base_text,
        )
        base_perch_view.grid(row=0, column=0, sticky="nsew")

        self.base_perch_controller = BasePerchController(
            base_perch_view,
            robot_request=self.robot.request,
            run_worker=self._run_worker,
            set_last_result=self.last_result_text.set,
            record_session_result=lambda key, value: self.session.__setitem__(key, value),
            is_user_editing=self._is_user_editing,
            set_var_if_present=self._set_var_if_present,
            controller_angles=self.controller_angles,
            base_profile_payload=lambda neutral: base_profile_payload(self.config_values.sender, neutral),
            base_status_payload=lambda: base_status_payload(self.config_values.sender),
            perch_payload=lambda: perch_payload(self.config_values.sender),
            save_perch_payload=lambda kind, value: save_perch_payload(self.config_values.sender, kind, value),
        )
        base_perch_view.on_capture_perch_photo = lambda: self.capture_photo("perch")

    def _build_ik_tab(self) -> None:
        self.ik_tab.columnconfigure(0, weight=1)
        self.ik_tab.rowconfigure(0, weight=1)

        ik_view = IkView(
            self.ik_tab,
            bind_edit_guard=self._bind_edit_guard,
            small_bold_font=self.ui_fonts["small_bold"],
            muted_color=MUTED,
            controller_angles=self.controller_angles,
            build_photo_panel=self._build_photo_panel,
        )
        ik_view.grid(row=0, column=0, sticky="nsew")

        self.ik_controller = IkController(
            ik_view,
            robot_request=self.robot.request,
            run_worker=self._run_worker,
            refresh_status=self.refresh_status,
            set_last_result=self.last_result_text.set,
            record_session_result=self._merge_session_result,
            is_user_editing=self._is_user_editing,
            set_var_if_present=self._set_var_if_present,
            controller_angles=self.controller_angles,
            move_all_servos=self.move_all_servos,
            capture_photo=self.capture_photo,
            ik_payload=lambda distance, z_height: ik_payload(self.config_values.sender, distance, z_height),
            save_hover_payload=lambda plane, kind, distance, elbow, wrist, twist: save_hover_payload(
                self.config_values.sender, plane, kind, distance, elbow, wrist, twist
            ),
        )

    def _build_visual_calibration_tab(self) -> None:
        self.visual_calibration_tab.columnconfigure(0, weight=1)
        self.visual_calibration_tab.rowconfigure(0, weight=1)

        visual_calibration_view = VisualCalibrationView(
            self.visual_calibration_tab,
            bind_edit_guard=self._bind_edit_guard,
            mono_font=self.ui_fonts["mono"],
        )
        visual_calibration_view.grid(row=0, column=0, sticky="nsew")

        self.visual_calibration_controller = VisualCalibrationController(
            visual_calibration_view,
            capture=lambda magnet_position: self.robot.capture_visual_calibration(
                CAPTURE_DIR, magnet_position=magnet_position
            ),
            run_worker=self._run_worker,
            set_app_status=self.status_text.set,
            set_last_result=self.last_result_text.set,
            record_session_result=lambda key, value: self.session.__setitem__(key, value),
            record_capture_path=self._record_capture_path,
        )

    def _build_reach_and_grab_tab(self) -> None:
        self.reach_and_grab_tab.columnconfigure(0, weight=1)
        self.reach_and_grab_tab.rowconfigure(0, weight=1)

        reach_and_grab_view = ReachAndGrabView(
            self.reach_and_grab_tab,
            bind_edit_guard=self._bind_edit_guard,
            mono_font=self.ui_fonts["mono"],
        )
        reach_and_grab_view.grid(row=0, column=0, sticky="nsew")

        self.reach_and_grab_controller = ReachAndGrabController(
            reach_and_grab_view,
            sender=lambda: self.config_values.sender,
            broker_connected=lambda: self.robot.state.broker_connected,
            run_reach_and_grab=lambda payload: self.robot.reach_and_grab(payload, CAPTURE_DIR),
            run_worker=self._run_worker,
            set_app_status=self.status_text.set,
            set_last_result=self.last_result_text.set,
            record_session_result=lambda key, value: self.session.__setitem__(key, value),
            record_capture_path=self._record_capture_path,
        )

    def _build_stencil_tab(self) -> None:
        self.stencil_tab.columnconfigure(0, weight=1)
        self.stencil_tab.rowconfigure(0, weight=1)

        stencil_view = StencilView(self.stencil_tab, bind_edit_guard=self._bind_edit_guard)
        stencil_view.grid(row=0, column=0, sticky="nsew")

        self.stencil_controller = StencilController(
            stencil_view,
            sender=lambda: self.config_values.sender,
            robot_request=self.robot.request,
            run_worker=self._run_worker,
            refresh_status=self.refresh_status,
            format_bool=self._format_bool,
        )

    # ---- Back-compat shims for the pre-MVC Stencil surface -----------------
    # tests.py and _sync_perch_from_calibrationvalues-style callers still
    # reach for these names; they now just forward to the controller. Remove
    # once every caller is updated to use self.stencil_controller directly.
    @property
    def stencil_status_box(self) -> tk.Text:
        return self.stencil_controller.view.status_box

    @property
    def stencil_points_box(self) -> tk.Text:
        return self.stencil_controller.view.points_box

    def _render_stencil_response(self, response: Dict[str, Any]) -> None:
        self.stencil_controller.render(response)

    # ---- Back-compat shims for the pre-MVC Visual Calibration surface ------
    # Remove once every caller uses self.visual_calibration_controller directly.
    @property
    def visual_calibration_result_box(self) -> tk.Text:
        return self.visual_calibration_controller.view.result_box

    @property
    def visual_calibration_status_text(self) -> tk.StringVar:
        return self.visual_calibration_controller.view.status_text

    def _render_visual_calibration_result(self, capture: Any) -> None:
        self.visual_calibration_controller._render_result(capture)

    # ---- Back-compat shims for the pre-MVC Reach and Grab surface ----------
    # Remove once every caller uses self.reach_and_grab_controller directly.
    @property
    def reach_and_grab_result_box(self) -> tk.Text:
        return self.reach_and_grab_controller.view.result_box

    @property
    def reach_and_grab_status_text(self) -> tk.StringVar:
        return self.reach_and_grab_controller.view.status_text

    @property
    def reach_and_grab_button(self) -> ttk.Button:
        return self.reach_and_grab_controller.view.run_button

    @property
    def reach_and_grab_current_action_id(self) -> str:
        return self.reach_and_grab_controller.state.current_action_id

    @reach_and_grab_current_action_id.setter
    def reach_and_grab_current_action_id(self, value: str) -> None:
        self.reach_and_grab_controller.state.current_action_id = value

    @property
    def reach_and_grab_request(self) -> Dict[str, Any]:
        return self.reach_and_grab_controller.state.request

    @reach_and_grab_request.setter
    def reach_and_grab_request(self, value: Dict[str, Any]) -> None:
        self.reach_and_grab_controller.state.request = value

    @property
    def reach_and_grab_progress(self) -> list:
        return self.reach_and_grab_controller.state.progress

    @reach_and_grab_progress.setter
    def reach_and_grab_progress(self, value: list) -> None:
        self.reach_and_grab_controller.state.progress = value

    @property
    def reach_and_grab_running(self) -> bool:
        return self.reach_and_grab_controller.state.running

    @reach_and_grab_running.setter
    def reach_and_grab_running(self, value: bool) -> None:
        self.reach_and_grab_controller.state.running = value

    def _render_reach_and_grab_terminal(self, response: Dict[str, Any], photo_path: Optional[Path] = None) -> None:
        self.reach_and_grab_controller._render_terminal(response, photo_path)

    # ---- Back-compat shims for the pre-MVC IK surface ----------------------
    # Remove once every caller uses self.ik_controller directly.
    @property
    def ik_rows(self) -> Dict[str, Dict[str, Dict[str, tk.Variable]]]:
        return self.ik_controller.rows

    # base_status is a real dependency, not just a test shim: the persistent
    # controller panel's own "Read state" button (_build_servo_controls)
    # calls it directly, sharing the same base_status_payload request the
    # Base + Perch tab's "Read base state" button uses.
    def base_status(self) -> None:
        self.base_perch_controller.read_base_state()

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

    def save_session_summary(self) -> None:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        self.session["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.session["last_photo"] = str(self.last_photo_path) if self.last_photo_path else ""
        self.session["calibrationvalues"] = self.robot.state.calibrationvalues
        path = SESSION_DIR / f"session_{time.strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(self.session, indent=2, sort_keys=True))
        self._info(f"Session summary saved to {path}")

    def capture_photo(self, label: str) -> None:
        def work() -> Path:
            return self.robot.capture_photo(label, CAPTURE_DIR)
        self._run_worker(f"capture {label} photo", work, on_success=self._display_photo)

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

    def _record_capture_path(self, path: Path) -> None:
        """Track a captured photo in the shared session/last-photo state.

        Several tabs (visual calibration, reach-and-grab, the persistent
        controller panel) all save into the same session capture list and
        share "the most recent photo" as a single pointer, so this stays on
        the main app rather than duplicated per controller.
        """
        self.last_photo_path = path
        if str(path) not in self.session["captures"]:
            self.session["captures"].append(str(path))

    def _merge_session_result(self, top_key: str, nested: Dict[str, Any]) -> None:
        """Deep-merge a {plane: {kind: {...}}} update into self.session[top_key].

        IK saves and validations record one row at a time, but every row
        under the same plane shares one dict in the session summary, so a
        later row must not clobber an earlier sibling row already recorded
        this session.
        """
        bucket = self.session.setdefault(top_key, {})
        for plane, by_kind in nested.items():
            bucket.setdefault(plane, {}).update(by_kind)

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
                    view = self.visual_calibration_controller.view
                    view.status_text.set(f"Failed — {error}")
                    view.set_result_text(f"Error: {error}")
                elif label == "reach and grab":
                    self.reach_and_grab_controller.handle_worker_error(error)
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
                self.visual_calibration_controller.display_photo_saved(Path(payload))
            elif kind == "visual_calibration_result":
                status = str(payload.get("status") or "result")
                self.last_result_text.set(f"Visual AI calibration: {status}")
            elif kind == "reach_and_grab_photo_saved":
                self.reach_and_grab_controller.display_photo_saved(Path(payload))
            elif kind == "reach_and_grab_progress":
                self.reach_and_grab_controller.handle_progress(payload)
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
        self.ik_controller.sync_from_calibrationvalues(values)
        self.base_perch_controller.sync_from_calibrationvalues(values)
        self.status_controller.render(values, hb, ready, state.last_error)

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
