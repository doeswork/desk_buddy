from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Dict, Optional


class StatusView(ttk.Frame):
    """Widgets for the Status tab.

    Builds layout only. status_summary_vars and system_status_vars are
    shared with the rest of the app (the connection-status ticker updates
    system_status_vars["heartbeat"] independently of this tab), so they are
    passed in rather than owned here -- one StringVar per fact, not one per
    place that fact is displayed.
    """

    def __init__(
        self,
        parent: tk.Widget,
        *,
        page_title_font,
        muted_color: str,
        ink_color: str,
        tiny_bold_font,
        body_bold_font,
        good_color: str,
        warn_color: str,
        bad_color: str,
        status_summary_vars: Dict[str, tk.StringVar],
        system_status_vars: Dict[str, tk.StringVar],
    ) -> None:
        super().__init__(parent)
        self._page_title_font = page_title_font
        self._muted_color = muted_color
        self._ink_color = ink_color
        self._tiny_bold_font = tiny_bold_font
        self._body_bold_font = body_bold_font
        self._good_color = good_color
        self._warn_color = warn_color
        self._bad_color = bad_color
        self.status_summary_vars = status_summary_vars
        self.system_status_vars = system_status_vars

        self.on_reconnect: Optional[Callable[[], None]] = None
        self.on_save_summary: Optional[Callable[[], None]] = None
        self.on_refresh_status: Optional[Callable[[], None]] = None

        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        heading = ttk.Frame(self)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        heading.columnconfigure(0, weight=1)
        copy = ttk.Frame(heading)
        copy.grid(row=0, column=0, sticky="w")
        ttk.Label(copy, text="Calibration status", font=self._page_title_font).pack(anchor="w")
        ttk.Label(
            copy,
            text="Saved values are distinguished from firmware defaults and missing calibration.",
            foreground=self._muted_color,
        ).pack(anchor="w", pady=(2, 0))
        actions = ttk.Frame(heading)
        actions.grid(row=0, column=1, sticky="e")
        ttk.Button(actions, text="Reconnect", command=lambda: self._dispatch(self.on_reconnect)).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(actions, text="Save Summary", command=lambda: self._dispatch(self.on_save_summary)).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(
            actions, text="Refresh Status", style="Accent.TButton", command=lambda: self._dispatch(self.on_refresh_status)
        ).pack(side="left")

        summary = ttk.Frame(self)
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
            ttk.Label(card, text=label.upper(), style="Muted.TLabel", font=self._tiny_bold_font).pack(anchor="w")
            ttk.Label(card, textvariable=self.status_summary_vars[key], style="Metric.TLabel").pack(anchor="w", pady=(3, 0))

        system = ttk.LabelFrame(self, text="Robot state", padding=(10, 8), style="Card.TLabelframe")
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

        checklist = ttk.LabelFrame(self, text="Saved preferences", padding=8, style="Card.TLabelframe")
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
        self.status_tree.tag_configure("group", background="#e9eef2", foreground=self._ink_color, font=self._body_bold_font)
        self.status_tree.tag_configure("saved", foreground=self._good_color)
        self.status_tree.tag_configure("default", foreground=self._warn_color)
        self.status_tree.tag_configure("missing", foreground=self._bad_color)
        self.status_tree.tag_configure("optional", foreground=self._muted_color)
        self.status_tree.grid(row=1, column=0, sticky="nsew")
        status_scroll = ttk.Scrollbar(checklist, orient="vertical", command=self.status_tree.yview)
        status_scroll.grid(row=1, column=1, sticky="ns")
        self.status_tree.configure(yscrollcommand=status_scroll.set)

    @staticmethod
    def _dispatch(callback: Optional[Callable[[], None]]) -> None:
        if callback is not None:
            callback()
