from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Optional

try:
    from ..models.ik_state import DEFAULT_DISTANCE_MM, PLANES
except ImportError:  # pragma: no cover - direct script execution
    from models.ik_state import DEFAULT_DISTANCE_MM, PLANES


class IkView(ttk.Frame):
    """Widgets for the IK tab.

    Builds layout only. Every action (import from controller, move, save,
    run validation, mark pass/fail) is a plain callback taking (plane, kind);
    the controller assigns them after construction.
    """

    def __init__(
        self,
        parent: tk.Widget,
        *,
        bind_edit_guard: Callable[[tk.Widget], None],
        small_bold_font,
        muted_color: str,
        controller_angles: Dict[str, tk.IntVar],
        build_photo_panel: Callable[[ttk.Frame, int, int], None],
    ) -> None:
        super().__init__(parent)
        self._bind_edit_guard = bind_edit_guard
        self._small_bold_font = small_bold_font
        self._muted_color = muted_color
        self._controller_angles = controller_angles
        self._build_photo_panel = build_photo_panel

        self.on_send_direct_ik: Optional[Callable[[], None]] = None
        self.on_move_controller_targets: Optional[Callable[[], None]] = None
        self.on_capture_photo: Optional[Callable[[], None]] = None
        self.on_import: Optional[Callable[[str, str], None]] = None
        self.on_move: Optional[Callable[[str, str], None]] = None
        self.on_save: Optional[Callable[[str, str], None]] = None
        self.on_run_validation: Optional[Callable[[str, str], None]] = None
        self.on_mark_pass: Optional[Callable[[str, str], None]] = None
        self.on_mark_fail: Optional[Callable[[str, str], None]] = None

        self.control_y = tk.IntVar(value=100)
        self.control_z = tk.IntVar(value=0)
        self.rows: Dict[str, Dict[str, Dict[str, tk.Variable]]] = {}

        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.LabelFrame(self, text="IK Workflow", padding=10)
        top.grid(row=0, column=0, sticky="nsew", padx=(0, 12), pady=(0, 8))
        top.columnconfigure(0, weight=1)
        ttk.Label(
            top,
            text=(
                "Send a calibrated IK target directly, or set the arm with the persistent controller "
                "and copy its targets into a calibration row."
            ),
            foreground=self._muted_color,
            wraplength=420,
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))

        direct = ttk.LabelFrame(top, text="Direct IK control", padding=8)
        direct.grid(row=1, column=0, sticky="ew", pady=(2, 6))
        direct.columnconfigure(1, weight=1)
        ttk.Label(direct, text="Y distance").grid(row=0, column=0, sticky="w", pady=3)
        y_entry = ttk.Spinbox(direct, from_=0, to=1000, increment=1, textvariable=self.control_y, width=8)
        y_entry.grid(row=0, column=1, sticky="ew", padx=(8, 4), pady=3)
        ttk.Label(direct, text="mm", foreground=self._muted_color).grid(row=0, column=2, sticky="w", pady=3)
        self._bind_edit_guard(y_entry)

        ttk.Label(direct, text="Z height").grid(row=1, column=0, sticky="w", pady=3)
        z_entry = ttk.Spinbox(direct, from_=0, to=50, increment=1, textvariable=self.control_z, width=8)
        z_entry.grid(row=1, column=1, sticky="ew", padx=(8, 4), pady=3)
        ttk.Label(direct, text="mm", foreground=self._muted_color).grid(row=1, column=2, sticky="w", pady=3)
        self._bind_edit_guard(z_entry)
        ttk.Button(
            direct,
            text="Send IK command",
            style="Accent.TButton",
            command=lambda: self._dispatch(self.on_send_direct_ik),
        ).grid(row=2, column=0, columnspan=3, sticky="ew", pady=(7, 0))
        ttk.Label(
            direct,
            text="Publishes controlik with distance=Y and z_height=Z. Firmware applies the saved IK calibration.",
            foreground=self._muted_color,
            wraplength=390,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

        ttk.Button(
            top, text="Move to controller targets", command=lambda: self._dispatch(self.on_move_controller_targets)
        ).grid(row=2, column=0, sticky="ew", pady=4)
        ttk.Button(top, text="Capture IK photo", command=lambda: self._dispatch(self.on_capture_photo)).grid(
            row=3, column=0, sticky="ew", pady=4
        )
        self._build_photo_panel(self, 0, 1)

        canvas = tk.Canvas(self, highlightthickness=0)
        scroll = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=1, column=0, columnspan=2, sticky="nsew")
        scroll.grid(row=1, column=2, sticky="ns")

        row = 0
        for plane, title in PLANES:
            group = ttk.LabelFrame(inner, text=title, padding=10)
            group.grid(row=row, column=0, sticky="ew", pady=(0, 12))
            group.columnconfigure(7, weight=1)
            self.rows[plane] = {}
            headers = ["Point", "Distance", "ELBOW", "WRIST", "TWIST", "Robot Controller", "Result"]
            for col, header in enumerate(headers):
                ttk.Label(group, text=header, font=self._small_bold_font).grid(row=0, column=col, sticky="w", padx=4)
            for idx, kind in enumerate(("min", "mid", "max"), start=1):
                defaults = DEFAULT_DISTANCE_MM[plane]
                vars_for_row: Dict[str, tk.Variable] = {
                    "distance": tk.DoubleVar(value=defaults[kind]),
                    "elbow": tk.IntVar(value=self._controller_angles["ELBOW"].get()),
                    "wrist": tk.IntVar(value=self._controller_angles["WRIST"].get()),
                    "twist": tk.IntVar(value=self._controller_angles["TWIST"].get()),
                    "result": tk.StringVar(value="Not recorded"),
                }
                self.rows[plane][kind] = vars_for_row
                ttk.Label(group, text=kind.upper()).grid(row=idx, column=0, sticky="w", padx=4, pady=4)
                for col, key in enumerate(("distance", "elbow", "wrist", "twist"), start=1):
                    entry = ttk.Entry(group, textvariable=vars_for_row[key], width=8)
                    entry.grid(row=idx, column=col, sticky="w", padx=4, pady=4)
                    self._bind_edit_guard(entry)
                ttk.Button(
                    group, text="Import", command=lambda p=plane, k=kind: self._dispatch_row(self.on_import, p, k)
                ).grid(row=idx, column=5, padx=2)
                ttk.Button(
                    group, text="Move", command=lambda p=plane, k=kind: self._dispatch_row(self.on_move, p, k)
                ).grid(row=idx, column=6, padx=2)
                if plane in ("z0", "z50"):
                    ttk.Button(
                        group, text="Save", command=lambda p=plane, k=kind: self._dispatch_row(self.on_save, p, k)
                    ).grid(row=idx, column=7, padx=2, sticky="w")
                else:
                    ttk.Button(
                        group,
                        text="Run IK",
                        command=lambda p=plane, k=kind: self._dispatch_row(self.on_run_validation, p, k),
                    ).grid(row=idx, column=7, padx=2, sticky="w")
                    ttk.Button(
                        group, text="Pass", command=lambda p=plane, k=kind: self._dispatch_row(self.on_mark_pass, p, k)
                    ).grid(row=idx, column=8, padx=2)
                    ttk.Button(
                        group, text="Fail", command=lambda p=plane, k=kind: self._dispatch_row(self.on_mark_fail, p, k)
                    ).grid(row=idx, column=9, padx=2)
                ttk.Label(group, textvariable=vars_for_row["result"]).grid(row=idx, column=10, sticky="w", padx=8)
            row += 1

    @staticmethod
    def _dispatch(callback: Optional[Callable[[], None]]) -> None:
        if callback is not None:
            callback()

    @staticmethod
    def _dispatch_row(callback: Optional[Callable[[str, str], None]], plane: str, kind: str) -> None:
        if callback is not None:
            callback(plane, kind)
