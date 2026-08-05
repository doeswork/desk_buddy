from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional


class StencilView(ttk.Frame):
    """Widgets for the Stencil tab.

    Builds layout only. Every action a user can take is a plain callback
    attribute (on_start, on_run_point, ...) left as None until the controller
    assigns them -- this view has no idea what MQTT is or what a robot is.
    """

    def __init__(self, parent: tk.Widget, *, bind_edit_guard: Callable[[tk.Widget], None]) -> None:
        super().__init__(parent, padding=12)
        self._bind_edit_guard = bind_edit_guard

        self.on_start: Optional[Callable[[], None]] = None
        self.on_run_point: Optional[Callable[[], None]] = None
        self.on_status_request: Optional[Callable[[], None]] = None
        self.on_cancel: Optional[Callable[[], None]] = None
        self.on_clear: Optional[Callable[[], None]] = None
        self.on_adjust: Optional[Callable[[], None]] = None
        self.on_adjust_previous_retry: Optional[Callable[[], None]] = None

        self.rotation_nudge = tk.DoubleVar(value=0.0)
        self.distance_nudge = tk.DoubleVar(value=0.0)

        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        controls = ttk.LabelFrame(self, text="Stencil Session", padding=10)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        for col in range(6):
            controls.columnconfigure(col, weight=1)
        ttk.Button(controls, text="Start Session", command=lambda: self._dispatch(self.on_start)).grid(
            row=0, column=0, sticky="ew", padx=(0, 6), pady=3
        )
        ttk.Button(controls, text="Run Current Point", command=lambda: self._dispatch(self.on_run_point)).grid(
            row=0, column=1, sticky="ew", padx=6, pady=3
        )
        ttk.Button(controls, text="Status", command=lambda: self._dispatch(self.on_status_request)).grid(
            row=0, column=2, sticky="ew", padx=6, pady=3
        )
        ttk.Button(controls, text="Cancel", command=lambda: self._dispatch(self.on_cancel)).grid(
            row=0, column=3, sticky="ew", padx=6, pady=3
        )
        ttk.Button(controls, text="Clear Saved Stencil", command=lambda: self._dispatch(self.on_clear)).grid(
            row=0, column=4, sticky="ew", padx=(6, 0), pady=3
        )

        adjust = ttk.LabelFrame(self, text="Adjustment For Current Point", padding=10)
        adjust.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        adjust.columnconfigure(1, weight=1)
        adjust.columnconfigure(3, weight=1)
        ttk.Label(adjust, text="Rotation nudge degrees").grid(row=0, column=0, sticky="w", padx=(0, 6))
        rotation_entry = ttk.Entry(adjust, textvariable=self.rotation_nudge, width=10)
        rotation_entry.grid(row=0, column=1, sticky="ew", padx=(0, 16))
        self._bind_edit_guard(rotation_entry)
        ttk.Label(adjust, text="Distance nudge mm").grid(row=0, column=2, sticky="w", padx=(0, 6))
        distance_entry = ttk.Entry(adjust, textvariable=self.distance_nudge, width=10)
        distance_entry.grid(row=0, column=3, sticky="ew", padx=(0, 16))
        self._bind_edit_guard(distance_entry)
        ttk.Button(adjust, text="Apply Adjustment", command=lambda: self._dispatch(self.on_adjust)).grid(
            row=0, column=4, sticky="ew", padx=(0, 6)
        )
        ttk.Button(
            adjust, text="Adjust & Retry Previous", command=lambda: self._dispatch(self.on_adjust_previous_retry)
        ).grid(row=0, column=5, sticky="ew")

        status = ttk.Frame(self)
        status.grid(row=2, column=0, sticky="nsew")
        status.columnconfigure(0, weight=1)
        status.columnconfigure(1, weight=1)
        status.rowconfigure(0, weight=1)

        summary = ttk.LabelFrame(status, text="Current Status", padding=8)
        summary.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        summary.columnconfigure(0, weight=1)
        summary.rowconfigure(0, weight=1)
        self.status_box = tk.Text(summary, height=18, wrap="word")
        self.status_box.grid(row=0, column=0, sticky="nsew")
        summary_scroll = ttk.Scrollbar(summary, orient="vertical", command=self.status_box.yview)
        summary_scroll.grid(row=0, column=1, sticky="ns")
        self.status_box.configure(yscrollcommand=summary_scroll.set)

        points = ttk.LabelFrame(status, text="Point progress", padding=8)
        points.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        points.columnconfigure(0, weight=1)
        points.rowconfigure(0, weight=1)
        self.points_box = tk.Text(points, height=18, wrap="none")
        self.points_box.grid(row=0, column=0, sticky="nsew")
        points_yscroll = ttk.Scrollbar(points, orient="vertical", command=self.points_box.yview)
        points_yscroll.grid(row=0, column=1, sticky="ns")
        points_xscroll = ttk.Scrollbar(points, orient="horizontal", command=self.points_box.xview)
        points_xscroll.grid(row=1, column=0, sticky="ew")
        self.points_box.configure(yscrollcommand=points_yscroll.set, xscrollcommand=points_xscroll.set)

    @staticmethod
    def _dispatch(callback: Optional[Callable[[], None]]) -> None:
        if callback is not None:
            callback()
