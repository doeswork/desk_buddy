from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Optional


class BasePerchView(ttk.Frame):
    """Widgets for the Base + Perch tab.

    Builds layout only. No MQTT, no business logic -- run/save/move
    decisions live in the controller.
    """

    def __init__(
        self,
        parent: tk.Widget,
        *,
        bind_edit_guard: Callable[[tk.Widget], None],
        page_title_font,
        muted_color: str,
        observed_base_text: tk.StringVar,
    ) -> None:
        super().__init__(parent)
        self._bind_edit_guard = bind_edit_guard
        self._page_title_font = page_title_font
        self._muted_color = muted_color
        # Shared with the persistent controller panel, which shows the same
        # live base telemetry next to its own "Read state" button -- there is
        # one base, so there must be one StringVar backing both displays.
        self.observed_base_text = observed_base_text

        self.on_run_profile_calibration: Optional[Callable[[], None]] = None
        self.on_read_base_state: Optional[Callable[[], None]] = None
        self.on_copy_controller_targets: Optional[Callable[[], None]] = None
        self.on_save_perch_pose: Optional[Callable[[], None]] = None
        self.on_move_to_saved_perch: Optional[Callable[[], None]] = None
        self.on_save_reach_landmarks: Optional[Callable[[], None]] = None
        self.on_capture_perch_photo: Optional[Callable[[], None]] = None

        self.base_neutral = tk.IntVar(value=90)
        self.perch_angle_vars: Dict[str, tk.Variable] = {
            "ELBOW": tk.IntVar(value=90),
            "WRIST": tk.IntVar(value=90),
            "TWIST": tk.IntVar(value=90),
        }
        self.perch_dist_vars: Dict[str, tk.Variable] = {
            "min": tk.DoubleVar(value=0),
            "mid": tk.DoubleVar(value=60),
            "max": tk.DoubleVar(value=120),
        }

        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        heading = ttk.Frame(self)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(heading, text="Base + perch", font=self._page_title_font).pack(anchor="w")
        ttk.Label(
            heading,
            text="Calibrate the rotating base first, then save a safe resting pose for the arm.",
            foreground=self._muted_color,
        ).pack(anchor="w", pady=(2, 0))

        sections = ttk.PanedWindow(self, orient="vertical")
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
        ttk.Button(
            base,
            text="Run profile calibration",
            style="Accent.TButton",
            command=lambda: self._dispatch(self.on_run_profile_calibration),
        ).grid(row=1, column=2, sticky="w", padx=(0, 8))
        ttk.Button(base, text="Read base state", command=lambda: self._dispatch(self.on_read_base_state)).grid(
            row=1, column=3, sticky="w"
        )
        ttk.Label(base, textvariable=self.observed_base_text, foreground=self._muted_color, wraplength=760).grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(10, 0)
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
        ttk.Button(
            pose, text="Copy controller targets", command=lambda: self._dispatch(self.on_copy_controller_targets)
        ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        ttk.Button(
            pose, text="Save perch pose", style="Accent.TButton", command=lambda: self._dispatch(self.on_save_perch_pose)
        ).grid(row=4, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Button(
            pose, text="Move to saved perch", command=lambda: self._dispatch(self.on_move_to_saved_perch)
        ).grid(row=5, column=0, columnspan=2, sticky="ew", pady=4)

        optional = ttk.LabelFrame(perch, text="Optional reach landmarks", padding=10, style="Card.TLabelframe")
        optional.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        optional.columnconfigure(1, weight=1)
        for idx, kind in enumerate(("min", "mid", "max")):
            ttk.Label(optional, text=kind.title()).grid(row=idx, column=0, sticky="w", pady=4)
            entry = ttk.Entry(optional, textvariable=self.perch_dist_vars[kind], width=8)
            entry.grid(row=idx, column=1, sticky="ew", padx=(8, 4), pady=4)
            ttk.Label(optional, text="mm", foreground=self._muted_color).grid(row=idx, column=2, sticky="w")
            self._bind_edit_guard(entry)
        ttk.Button(
            optional, text="Save reach landmarks", command=lambda: self._dispatch(self.on_save_reach_landmarks)
        ).grid(row=3, column=0, columnspan=3, sticky="ew", pady=(10, 4))
        ttk.Button(
            optional, text="Capture perch photo", command=lambda: self._dispatch(self.on_capture_perch_photo)
        ).grid(row=4, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Label(
            optional,
            text="These landmarks are optional and do not replace the IK calibration points.",
            foreground=self._muted_color,
            wraplength=300,
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))
        sections.add(perch, weight=3)

    @staticmethod
    def _dispatch(callback: Optional[Callable[[], None]]) -> None:
        if callback is not None:
            callback()
