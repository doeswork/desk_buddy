from __future__ import annotations

import json
from pathlib import Path
import queue
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
        stencil_payload,
    )
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
        stencil_payload,
    )


CAPTURE_DIR = Path(__file__).resolve().parent / "captures"
SESSION_DIR = Path(__file__).resolve().parent / "sessions"
MAX_TOPIC_LOG_LINES = 500
MAX_TOPIC_LOG_PAYLOAD_CHARS = 900


def format_topic_log_event(kind: str, payload: Any) -> Optional[str]:
    """Return a compact topic-log line body, or None when it should be hidden."""
    if kind == "message" and isinstance(payload, dict):
        if payload.get("log") == "heartbeat":
            return None
        status = payload.get("status") or payload.get("photo") or payload.get("debug") or payload.get("log") or "message"
        action_id = payload.get("action_id", "")
        return _compact_log_line("IN", str(status), str(action_id), payload)

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
        self.title("Desk Buddy Calibration Wizard")
        self.geometry("1280x960")
        self.minsize(1100, 760)

        # UI text scaling. Some systems default the Tk named fonts to the
        # "fixed" bitmap family, which cannot be resized (zoom looks like it
        # does nothing). Move every named font onto a scalable TrueType family
        # first, then record its base size so zoom re-derives from the original
        # rather than compounding.
        self.ui_scale = 1.0
        self._base_font_sizes: Dict[str, int] = {}
        scalable_family = self._pick_scalable_family()
        for font_name in tkfont.names(self):
            font = tkfont.nametofont(font_name)
            size = font.cget("size") or font.actual("size")
            if size <= 0:
                size = 10
            if scalable_family:
                font.configure(family=scalable_family, size=size)
            self._base_font_sizes[font_name] = size
        self.bind_all("<Control-plus>", lambda _e: self._bump_ui_scale(0.1))
        self.bind_all("<Control-KP_Add>", lambda _e: self._bump_ui_scale(0.1))
        self.bind_all("<Control-equal>", lambda _e: self._bump_ui_scale(0.1))
        self.bind_all("<Control-minus>", lambda _e: self._bump_ui_scale(-0.1))
        self.bind_all("<Control-KP_Subtract>", lambda _e: self._bump_ui_scale(-0.1))
        self.bind_all("<Control-0>", lambda _e: self._set_ui_scale(1.0))

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
        self.base_angle = tk.DoubleVar(value=0)
        self.base_degrees = tk.DoubleVar(value=10)
        self.base_steps = tk.IntVar(value=1)
        self.base_direction = tk.StringVar(value="LEFT")
        self.base_speed = tk.StringVar(value="slow")
        self.observed_base_text = tk.StringVar(value="Base: no status yet")
        self.stencil_rotation_nudge = tk.DoubleVar(value=0.0)
        self.stencil_distance_nudge = tk.DoubleVar(value=0.0)
        self.stencil_status: Dict[str, Any] = {}
        self.live_mode = tk.BooleanVar(value=False)
        self.user_editing_until = 0.0
        self.last_photo_path: Optional[Path] = None
        self.photo_image: Any = None
        self.photo_labels: list[ttk.Label] = []
        self.topic_log_lines: list[str] = []
        self._last_ik_sync_signature = ""
        self.session: Dict[str, Any] = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "captures": [], "ik_validation": {}}

        self.status_text = tk.StringVar(value="Not connected")
        self.robot_text = tk.StringVar(value="Robot offline")
        self.health_text = tk.StringVar(value="No heartbeat yet")
        self.last_result_text = tk.StringVar(value="No command sent yet")

        self._build_ui()
        self._load_settings_into_form()
        self.after(150, self._process_events)
        self.after(1000, self._tick_health)

    def _queue_event(self, kind: str, payload: Any) -> None:
        self.events.put((kind, payload))

    def _pick_scalable_family(self) -> Optional[str]:
        # Prefer a common scalable TrueType family; bitmap families like
        # "fixed" ignore size changes.
        available = set(tkfont.families(self))
        # Keep the current family if it is already scalable (system Tk usually
        # defaults to Liberation Sans); only override when stuck on a bitmap
        # family such as "fixed".
        current = tkfont.nametofont("TkDefaultFont").actual("family")
        if current and current.lower() not in ("fixed", ""):
            return current
        for candidate in ("Liberation Sans", "Noto Sans", "DejaVu Sans", "Arial", "Helvetica"):
            if candidate in available:
                return candidate
        return None

    def _bump_ui_scale(self, delta: float) -> None:
        self._set_ui_scale(self.ui_scale + delta)

    def _set_ui_scale(self, scale: float) -> None:
        # Clamp to a sane range and re-scale every named font from its base
        # size. ttk widgets in this app resolve to these named fonts, so this
        # resizes labels, buttons, entries, Text boxes, and menus together.
        self.ui_scale = max(0.5, min(4.0, round(scale, 2)))
        for font_name, base_size in self._base_font_sizes.items():
            if not base_size:
                continue
            sign = 1 if base_size > 0 else -1
            scaled = sign * max(1, int(round(abs(base_size) * self.ui_scale)))
            tkfont.nametofont(font_name).configure(size=scaled)
        if hasattr(self, "scale_text"):
            self.scale_text.set(f"Text: {int(round(self.ui_scale * 100))}%")

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        banner = ttk.Frame(self, padding=(10, 8))
        banner.grid(row=0, column=0, sticky="ew")
        banner.columnconfigure(4, weight=1)
        ttk.Label(banner, textvariable=self.status_text, font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 16))
        ttk.Label(banner, textvariable=self.robot_text).grid(row=0, column=1, sticky="w", padx=(0, 16))
        ttk.Label(banner, textvariable=self.health_text).grid(row=0, column=2, sticky="w", padx=(0, 16))
        ttk.Button(banner, text="Refresh Status", command=self.refresh_status).grid(row=0, column=3, sticky="e")

        zoom = ttk.Frame(banner)
        zoom.grid(row=0, column=5, sticky="e")
        self.scale_text = tk.StringVar(value=f"Text: {int(round(self.ui_scale * 100))}%")
        ttk.Button(zoom, text="A-", width=3, command=lambda: self._bump_ui_scale(-0.1)).pack(side="left")
        ttk.Button(zoom, text="A+", width=3, command=lambda: self._bump_ui_scale(0.1)).pack(side="left", padx=(4, 0))
        ttk.Button(zoom, text="Reset", command=lambda: self._set_ui_scale(1.0)).pack(side="left", padx=(4, 8))
        ttk.Label(zoom, textvariable=self.scale_text).pack(side="left")

        self.main_pane = ttk.PanedWindow(self, orient="vertical")
        self.main_pane.grid(row=1, column=0, sticky="nsew")

        self.notebook = ttk.Notebook(self.main_pane)
        self.setup_tab = ttk.Frame(self.notebook, padding=12)
        self.health_tab = ttk.Frame(self.notebook, padding=12)
        self.perch_tab = ttk.Frame(self.notebook, padding=12)
        self.ik_tab = ttk.Frame(self.notebook, padding=12)
        self.stencil_tab = ttk.Frame(self.notebook, padding=12)
        self.final_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.setup_tab, text="1. MQTT Setup")
        self.notebook.add(self.health_tab, text="2. Health")
        self.notebook.add(self.perch_tab, text="3. Perch")
        self.notebook.add(self.ik_tab, text="4. IK Calibration")
        self.notebook.add(self.stencil_tab, text="5. Stencil")
        self.notebook.add(self.final_tab, text="6. Final Check")
        self.main_pane.add(self.notebook, weight=4)

        self._build_setup_tab()
        self._build_health_tab()
        self._build_perch_tab()
        self._build_ik_tab()
        self._build_stencil_tab()
        self._build_final_tab()
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
        log_frame = ttk.LabelFrame(self.main_pane, text="Topic Log", padding=(8, 6))
        self.main_pane.add(log_frame, weight=1)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(log_frame)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(toolbar, text="MQTT and app events, heartbeat hidden").pack(side="left")
        ttk.Button(toolbar, text="Clear Log", command=self._clear_log).pack(side="right", padx=(6, 0))
        ttk.Button(toolbar, text="Copy Log", command=self._copy_log).pack(side="right")

        body = ttk.Frame(log_frame)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.topic_log = tk.Text(body, height=8, wrap="none", state="disabled")
        self.topic_log.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(body, orient="vertical", command=self.topic_log.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(body, orient="horizontal", command=self.topic_log.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        self.topic_log.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.topic_log_yscroll = yscroll
        self.topic_log_xscroll = xscroll

    def _build_health_tab(self) -> None:
        self.health_tab.columnconfigure(0, weight=1)
        controls = ttk.Frame(self.health_tab)
        controls.grid(row=0, column=0, sticky="ew")
        ttk.Button(controls, text="Refresh Calibration Values", command=self.refresh_status).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Reconnect", command=self.connect_mqtt).pack(side="left")
        base = ttk.LabelFrame(self.health_tab, text="Base Rotation", padding=10)
        base.grid(row=1, column=0, sticky="ew", pady=(10, 6))
        ttk.Button(base, text="Run Base Profile Calibration", command=self.run_base_profile).pack(side="left", padx=(0, 8))
        ttk.Label(base, text="Neutral").pack(side="left")
        neutral_entry = ttk.Entry(base, textvariable=self.base_neutral, width=5)
        neutral_entry.pack(side="left", padx=(4, 12))
        self._bind_edit_guard(neutral_entry)
        ttk.Button(base, text="Base Status", command=self.base_status).pack(side="left", padx=(0, 8))
        ttk.Label(base, text="Angle").pack(side="left")
        angle_entry = ttk.Entry(base, textvariable=self.base_angle, width=7)
        angle_entry.pack(side="left", padx=(4, 4))
        self._bind_edit_guard(angle_entry)
        ttk.Button(base, text="Move Angle", command=self.move_base_angle).pack(side="left")
        ttk.Label(base, textvariable=self.observed_base_text).pack(side="left", padx=(16, 0))
        ttk.Label(self.health_tab, textvariable=self.last_result_text).grid(row=2, column=0, sticky="w", pady=(4, 6))
        health_body = ttk.Frame(self.health_tab)
        health_body.grid(row=3, column=0, sticky="nsew")
        health_body.columnconfigure(0, weight=1)
        health_body.rowconfigure(0, weight=1)
        self.health_box = tk.Text(health_body, height=28, wrap="word")
        self.health_box.grid(row=0, column=0, sticky="nsew")
        health_scroll = ttk.Scrollbar(health_body, orient="vertical", command=self.health_box.yview)
        health_scroll.grid(row=0, column=1, sticky="ns")
        self.health_box.configure(yscrollcommand=health_scroll.set)
        self.health_scroll = health_scroll
        self.health_tab.rowconfigure(3, weight=1)

    def _build_servo_controls(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Robot Controller", padding=10)
        frame.grid(row=0, column=0, sticky="new", padx=(0, 12))
        ttk.Label(frame, text="Target").grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(frame, text="Observed").grid(row=0, column=6, sticky="w", padx=(12, 0))
        for idx, name in enumerate(("ELBOW", "WRIST", "TWIST")):
            row = idx + 1
            ttk.Label(frame, text=name).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Scale(frame, from_=0, to=180, variable=self.controller_angles[name], orient="horizontal").grid(row=row, column=1, sticky="ew", padx=8)
            spinbox = ttk.Spinbox(frame, from_=0, to=180, textvariable=self.controller_angles[name], width=5)
            spinbox.grid(row=row, column=2, sticky="w")
            self._bind_edit_guard(spinbox)
            ttk.Button(frame, text="-5", command=lambda n=name: self._nudge(n, -5)).grid(row=row, column=3, padx=(8, 2))
            ttk.Button(frame, text="+5", command=lambda n=name: self._nudge(n, 5)).grid(row=row, column=4, padx=2)
            ttk.Button(frame, text="Move", command=lambda n=name: self.move_servo(n)).grid(row=row, column=5, padx=(8, 0))
            ttk.Label(frame, textvariable=self.observed_angles[name]).grid(row=row, column=6, sticky="w", padx=(12, 0))
        frame.columnconfigure(1, weight=1)
        ttk.Button(frame, text="Move All", command=self.move_all_servos).grid(row=4, column=1, sticky="w", pady=(10, 0))
        ttk.Button(frame, text="Sync Controller From Robot", command=self.sync_controller_from_robot).grid(row=4, column=2, columnspan=2, sticky="w", padx=(8, 0), pady=(10, 0))
        ttk.Checkbutton(frame, text="Live/safety bypass", variable=self.live_mode).grid(row=4, column=4, columnspan=3, sticky="w", pady=(10, 0))

        gripper_row = ttk.Frame(frame)
        gripper_row.grid(row=5, column=0, columnspan=7, sticky="ew", pady=(12, 0))
        ttk.Button(gripper_row, text="Grab", command=lambda: self.run_gripper("GRAB")).pack(side="left", padx=(0, 6))
        ttk.Button(gripper_row, text="Soft Grab", command=lambda: self.run_gripper("SOFTHOLD")).pack(side="left", padx=(0, 6))
        ttk.Button(gripper_row, text="Drop", command=lambda: self.run_gripper("DROP")).pack(side="left")
        ttk.Label(gripper_row, text="Observed gripper").pack(side="left", padx=(16, 4))
        ttk.Label(gripper_row, textvariable=self.observed_angles["GRIPPER"]).pack(side="left")

        base = ttk.LabelFrame(frame, text="Base Rotation", padding=8)
        base.grid(row=6, column=0, columnspan=7, sticky="ew", pady=(12, 0))
        base.columnconfigure(1, weight=1)
        ttk.Label(base, text="Speed").grid(row=0, column=0, sticky="w", pady=3)
        ttk.OptionMenu(base, self.base_speed, self.base_speed.get(), "veryslow", "slow", "regular", "fast", "superfast").grid(row=0, column=1, sticky="ew", padx=(6, 10), pady=3)
        ttk.Label(base, text="Direction").grid(row=0, column=2, sticky="w", pady=3)
        ttk.OptionMenu(base, self.base_direction, self.base_direction.get(), "LEFT", "RIGHT").grid(row=0, column=3, sticky="ew", padx=(6, 0), pady=3)

        ttk.Label(base, text="Angle").grid(row=1, column=0, sticky="w", pady=3)
        angle_entry = ttk.Entry(base, textvariable=self.base_angle, width=8)
        angle_entry.grid(row=1, column=1, sticky="ew", padx=(6, 10), pady=3)
        self._bind_edit_guard(angle_entry)
        ttk.Button(base, text="Move Angle", command=self.move_base_angle).grid(row=1, column=2, columnspan=2, sticky="ew", pady=3)

        ttk.Label(base, text="Degrees").grid(row=2, column=0, sticky="w", pady=3)
        degrees_entry = ttk.Entry(base, textvariable=self.base_degrees, width=8)
        degrees_entry.grid(row=2, column=1, sticky="ew", padx=(6, 10), pady=3)
        self._bind_edit_guard(degrees_entry)
        ttk.Button(base, text="Move Degrees", command=self.move_base_degrees).grid(row=2, column=2, columnspan=2, sticky="ew", pady=3)

        ttk.Label(base, text="Steps").grid(row=3, column=0, sticky="w", pady=3)
        steps_entry = ttk.Entry(base, textvariable=self.base_steps, width=8)
        steps_entry.grid(row=3, column=1, sticky="ew", padx=(6, 10), pady=3)
        self._bind_edit_guard(steps_entry)
        ttk.Button(base, text="Move Steps", command=self.move_base_steps).grid(row=3, column=2, columnspan=2, sticky="ew", pady=3)
        ttk.Button(base, text="Base Status", command=self.base_status).grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(base, textvariable=self.observed_base_text).grid(row=4, column=1, columnspan=3, sticky="w", padx=(6, 0), pady=(8, 0))

    def _build_photo_panel(self, parent: ttk.Frame, row: int, column: int) -> None:
        panel = ttk.LabelFrame(parent, text="Camera", padding=10)
        panel.grid(row=row, column=column, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        photo_label = ttk.Label(panel, text="No photo captured yet", anchor="center")
        photo_label.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        self.photo_labels.append(photo_label)
        panel.rowconfigure(0, weight=1)
        ttk.Button(panel, text="Capture Photo", command=lambda: self.capture_photo("manual")).grid(row=1, column=0, sticky="ew")

    def _build_perch_tab(self) -> None:
        self.perch_tab.columnconfigure(1, weight=1)
        self.perch_tab.rowconfigure(0, weight=1)
        self._build_servo_controls(self.perch_tab)

        actions = ttk.LabelFrame(self.perch_tab, text="Perch Workflow", padding=10)
        actions.grid(row=0, column=1, sticky="nsew", padx=(0, 12))
        actions.columnconfigure(1, weight=1)
        ttk.Button(actions, text="Move Robot To Controller Values", command=self.move_all_servos).grid(row=0, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Button(actions, text="Capture Perch Photo", command=lambda: self.capture_photo("perch")).grid(row=1, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Button(actions, text="Move To Saved Perch", command=self.move_saved_perch).grid(row=2, column=0, columnspan=2, sticky="ew", pady=4)

        ttk.Label(actions, text="Perch Values To Save", font=("TkDefaultFont", 9, "bold")).grid(row=3, column=0, columnspan=2, sticky="w", pady=(14, 4))
        for idx, name in enumerate(("ELBOW", "WRIST", "TWIST"), start=4):
            ttk.Label(actions, text=name).grid(row=idx, column=0, sticky="w", pady=4)
            entry = ttk.Entry(actions, textvariable=self.perch_angle_vars[name], width=8)
            entry.grid(row=idx, column=1, sticky="w", pady=4)
            self._bind_edit_guard(entry)
        ttk.Button(actions, text="Copy Controller Angles To Perch Fields", command=self.use_controller_for_perch).grid(row=7, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        ttk.Button(actions, text="Save Perch Values", command=self.save_perch_angles).grid(row=8, column=0, columnspan=2, sticky="ew", pady=(0, 10))

        self.perch_dist_vars = {
            "min": tk.DoubleVar(value=0),
            "mid": tk.DoubleVar(value=60),
            "max": tk.DoubleVar(value=120),
        }
        for idx, kind in enumerate(("min", "mid", "max"), start=9):
            ttk.Label(actions, text=f"Perch {kind.upper()} distance").grid(row=idx, column=0, sticky="w", pady=4)
            entry = ttk.Entry(actions, textvariable=self.perch_dist_vars[kind], width=8)
            entry.grid(row=idx, column=1, sticky="w", pady=4)
            self._bind_edit_guard(entry)
        ttk.Button(actions, text="Save Perch Distances", command=self.save_perch_distances).grid(row=12, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        self._build_photo_panel(self.perch_tab, 0, 2)

    def _build_ik_tab(self) -> None:
        self.ik_tab.columnconfigure(1, weight=1)
        self.ik_tab.columnconfigure(2, weight=1)
        self.ik_tab.rowconfigure(1, weight=1)
        self._build_servo_controls(self.ik_tab)
        top = ttk.LabelFrame(self.ik_tab, text="IK Workflow", padding=10)
        top.grid(row=0, column=1, sticky="nsew", padx=(0, 12), pady=(0, 8))
        top.columnconfigure(0, weight=1)
        ttk.Button(top, text="Move Robot To Controller Values", command=self.move_all_servos).grid(row=0, column=0, sticky="ew", pady=4)
        ttk.Button(top, text="Capture IK Photo", command=lambda: self.capture_photo("ik")).grid(row=1, column=0, sticky="ew", pady=4)
        self._build_photo_panel(self.ik_tab, 0, 2)

        self.ik_rows: Dict[str, Dict[str, Dict[str, tk.Variable]]] = {}
        canvas = tk.Canvas(self.ik_tab, highlightthickness=0)
        scroll = ttk.Scrollbar(self.ik_tab, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=1, column=0, columnspan=3, sticky="nsew")
        scroll.grid(row=1, column=3, sticky="ns")

        row = 0
        for plane, title in (("z0", "z=0 saved firmware points"), ("z50", "z=50 saved firmware points"), ("z25", "z=25 validation only")):
            group = ttk.LabelFrame(inner, text=title, padding=10)
            group.grid(row=row, column=0, sticky="ew", pady=(0, 12))
            group.columnconfigure(7, weight=1)
            self.ik_rows[plane] = {}
            headers = ["Point", "Distance", "ELBOW", "WRIST", "TWIST", "Actions", "Result"]
            for col, header in enumerate(headers):
                ttk.Label(group, text=header, font=("TkDefaultFont", 9, "bold")).grid(row=0, column=col, sticky="w", padx=4)
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
                ttk.Button(group, text="Copy Controller Angles To This Row", command=lambda p=plane, k=kind: self.use_controller_for_ik(p, k)).grid(row=idx, column=5, padx=2)
                ttk.Button(group, text="Move", command=lambda p=plane, k=kind: self.move_ik_row(p, k)).grid(row=idx, column=6, padx=2)
                if plane in ("z0", "z50"):
                    ttk.Button(group, text="Save", command=lambda p=plane, k=kind: self.save_ik_row(p, k)).grid(row=idx, column=7, padx=2, sticky="w")
                else:
                    ttk.Button(group, text="Run IK", command=lambda p=plane, k=kind: self.run_ik_validation(p, k)).grid(row=idx, column=7, padx=2, sticky="w")
                    ttk.Button(group, text="Pass", command=lambda p=plane, k=kind: self.mark_ik_validation(p, k, True)).grid(row=idx, column=8, padx=2)
                    ttk.Button(group, text="Fail", command=lambda p=plane, k=kind: self.mark_ik_validation(p, k, False)).grid(row=idx, column=9, padx=2)
                ttk.Label(group, textvariable=vars_for_row["result"]).grid(row=idx, column=10, sticky="w", padx=8)
            row += 1

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

        points = ttk.LabelFrame(status, text="9-Point Progress", padding=8)
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

    def _build_final_tab(self) -> None:
        self.final_tab.columnconfigure(0, weight=1)
        controls = ttk.Frame(self.final_tab)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(controls, text="Run Final Check", command=self.final_check).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Save Session Summary", command=self.save_session_summary).pack(side="left")
        final_body = ttk.Frame(self.final_tab)
        final_body.grid(row=1, column=0, sticky="nsew")
        final_body.columnconfigure(0, weight=1)
        final_body.rowconfigure(0, weight=1)
        self.final_box = tk.Text(final_body, height=28, wrap="word")
        self.final_box.grid(row=0, column=0, sticky="nsew")
        final_scroll = ttk.Scrollbar(final_body, orient="vertical", command=self.final_box.yview)
        final_scroll.grid(row=0, column=1, sticky="ns")
        self.final_box.configure(yscrollcommand=final_scroll.set)
        self.final_scroll = final_scroll
        self.final_tab.rowconfigure(1, weight=1)

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
        self._run_worker("base profile calibration", lambda: self.robot.request(base_profile_payload(self.config_values.sender, neutral)))

    def base_status(self) -> None:
        self._run_worker("base status", lambda: self.robot.request(base_status_payload(self.config_values.sender)))

    def move_base_angle(self) -> None:
        angle = float(self.base_angle.get())
        if angle < 0 or angle >= 360:
            messagebox.showerror("Calibration Wizard", "Base angle must be between 0 and 359.999 degrees.")
            return
        self._run_worker("move base angle", lambda: self.robot.request(base_angle_payload(self.config_values.sender, angle, self.base_speed.get())))

    def move_base_degrees(self) -> None:
        degrees = float(self.base_degrees.get())
        if degrees <= 0:
            messagebox.showerror("Calibration Wizard", "Base degrees must be greater than 0.")
            return
        self._run_worker(
            "move base degrees",
            lambda: self.robot.request(base_degrees_payload(self.config_values.sender, self.base_direction.get(), degrees, self.base_speed.get())),
        )

    def move_base_steps(self) -> None:
        steps = int(self.base_steps.get())
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

    def final_check(self) -> None:
        def after(values: Dict[str, Any]) -> None:
            checks = {
                "base rotation ready": bool(values.get("base_rotation_ready")),
                "perch configured": bool(values.get("perch_configured")),
                "IK z=0 calibrated": bool(values.get("ik_hover_calibrated")),
                "IK z=50 calibrated": bool(values.get("ik_z50_calibrated")),
                "stencil calibrated": bool(values.get("stencil_calibrated")),
                "motion calibration ready": bool(values.get("motion_calibration_ready")),
                "initial calibration ready": bool(values.get("initial_calibration_ready")),
            }
            self.final_box.delete("1.0", tk.END)
            for name, ok in checks.items():
                self.final_box.insert(tk.END, f"{'PASS' if ok else 'TODO'}  {name}\n")
            self.final_box.insert(tk.END, "\nCalibration values:\n")
            self.final_box.insert(tk.END, json.dumps(values, indent=2, sort_keys=True))
        self._run_worker("final check", self.robot.refresh_calibrationvalues, on_success=after)

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
            return
        image = Image.open(path)
        image.thumbnail((360, 300))
        self.photo_image = ImageTk.PhotoImage(image)
        for label in self.photo_labels:
            label.configure(image=self.photo_image, text="")

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
        self.after(150, self._process_events)

    def _append_log(self, kind: str, payload: Any) -> None:
        line_body = format_topic_log_event(kind, payload)
        if not line_body:
            return
        should_follow = self.topic_log.yview()[1] >= 0.98
        line = f"[{time.strftime('%H:%M:%S')}] {line_body}"
        self.topic_log_lines.append(line)
        if len(self.topic_log_lines) > MAX_TOPIC_LOG_LINES:
            self.topic_log_lines = self.topic_log_lines[-MAX_TOPIC_LOG_LINES:]

        self.topic_log.configure(state="normal")
        self.topic_log.delete("1.0", tk.END)
        self.topic_log.insert(tk.END, "\n".join(self.topic_log_lines))
        if self.topic_log_lines:
            self.topic_log.insert(tk.END, "\n")
        if should_follow:
            self.topic_log.see(tk.END)
        self.topic_log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.topic_log_lines.clear()
        self.topic_log.configure(state="normal")
        self.topic_log.delete("1.0", tk.END)
        self.topic_log.configure(state="disabled")

    def _copy_log(self) -> None:
        self.clipboard_clear()
        self.clipboard_append("\n".join(self.topic_log_lines))
        self.last_result_text.set("Topic log copied to clipboard")

    def _tick_health(self) -> None:
        age = self.robot.state.heartbeat_age()
        if age is None:
            self.robot_text.set("Robot offline")
            self.health_text.set("No heartbeat yet")
        else:
            online = age < 12
            self.robot.state.robot_online = online
            self.robot_text.set("Robot online" if online else "Robot stale")
            self.health_text.set(f"Heartbeat age: {age:.1f}s")
        self.after(1000, self._tick_health)

    def _render_state(self) -> None:
        state = self.robot.state
        self.status_text.set("MQTT connected" if state.broker_connected else "MQTT disconnected")
        values = state.calibrationvalues
        hb = state.last_heartbeat
        ready = state.last_ready
        self._update_observed_telemetry(values, hb, state.last_response)
        self._sync_ik_rows_from_calibrationvalues(values)
        health = {
            "broker_connected": state.broker_connected,
            "robot_online": state.robot_online,
            "heartbeat_age": state.heartbeat_age(),
            "firmware_version": hb.get("firmware_version") or ready.get("firmware_version"),
            "ota_state": hb.get("ota_state") or ready.get("ota_state"),
            "last_reset_reason": ready.get("last_reset_reason"),
            "current_free_heap": ready.get("current_free_heap"),
            "ELBOW_ANGLE": hb.get("ELBOW_ANGLE", values.get("ELBOW_ANGLE")),
            "WRIST_ANGLE": hb.get("WRIST_ANGLE", values.get("WRIST_ANGLE")),
            "TWIST_ANGLE": hb.get("TWIST_ANGLE", values.get("TWIST_ANGLE")),
            "GRIPPER_ANGLE": hb.get("GRIPPER_ANGLE", values.get("GRIPPER_ANGLE")),
            "base_rotation_ready": values.get("base_rotation_ready"),
            "perch_configured": values.get("perch_configured"),
            "ik_hover_calibrated": values.get("ik_hover_calibrated"),
            "ik_z50_calibrated": values.get("ik_z50_calibrated"),
            "hover_over_min": values.get("hover_over_min"),
            "hover_over_mid": values.get("hover_over_mid"),
            "hover_over_max": values.get("hover_over_max"),
            "hover_min_120": values.get("hover_min_120"),
            "hover_mid_120": values.get("hover_mid_120"),
            "hover_max_120": values.get("hover_max_120"),
            "stencil_calibrated": values.get("stencil_calibrated"),
            "motion_calibration_ready": values.get("motion_calibration_ready"),
            "initial_calibration_ready": values.get("initial_calibration_ready"),
            "last_error": state.last_error,
        }
        self.health_box.delete("1.0", tk.END)
        self.health_box.insert(tk.END, json.dumps(health, indent=2, sort_keys=True, default=str))

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
            self.observed_angles[target_key].set(self._format_observed_value(value))

        base_status = last_response.get("base_rotation")
        if not isinstance(base_status, dict):
            base_status = {}
        angle = base_status.get("baseAngleDegrees")
        counts = base_status.get("basePositionCounts", values.get("base_rotation_lastCounts"))
        calibrated = base_status.get("calibrated", values.get("base_rotation_calibrated"))
        trusted = base_status.get("positionTrusted", values.get("base_rotation_lastValid"))
        if angle is not None:
            self.observed_base_text.set(
                f"Base: {self._format_observed_value(angle)} deg, counts {self._format_observed_value(counts)}, "
                f"calibrated {self._format_bool(calibrated)}, trusted {self._format_bool(trusted)}"
            )
        else:
            self.observed_base_text.set(
                f"Base: counts {self._format_observed_value(counts)}, "
                f"calibrated {self._format_bool(calibrated)}, trusted {self._format_bool(trusted)}"
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
