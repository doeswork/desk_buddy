from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Optional


class ReachAndGrabView(ttk.Frame):
    """Widgets for the Reach and Grab tab.

    Builds layout only. No MQTT, no business logic -- run and image-load
    decisions live in the controller.
    """

    def __init__(
        self,
        parent: tk.Widget,
        *,
        bind_edit_guard: Callable[[tk.Widget], None],
        mono_font: Any,
    ) -> None:
        super().__init__(parent)
        self._bind_edit_guard = bind_edit_guard
        self._mono_font = mono_font

        self.on_run: Optional[Callable[[], None]] = None

        self.target = tk.StringVar(value="")
        self.use_model = tk.BooleanVar(value=False)
        self.model_name = tk.StringVar(value="")
        self.box_threshold = tk.DoubleVar(value=0.35)
        self.text_threshold = tk.DoubleVar(value=0.25)
        self.magnet_position = tk.IntVar(value=1)
        self.workflow_id = tk.StringVar(value="")
        self.workflow_event_id = tk.StringVar(value="")
        self.status_text = tk.StringVar(value="Not run yet")
        self.action_text = tk.StringVar(value="Action ID: —")
        self._photo_image: Any = None

        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        request = ttk.LabelFrame(self, text="Automatic reach-and-grab request", padding=12, style="Card.TLabelframe")
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
            foreground="#5b6b79",
            wraplength=560,
            justify="left",
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 12))

        ttk.Label(request, text="Object description").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        target_entry = ttk.Entry(request, textvariable=self.target)
        target_entry.grid(row=1, column=1, columnspan=3, sticky="ew", pady=4)
        self._bind_edit_guard(target_entry)

        ttk.Checkbutton(request, text="Use configured learned model", variable=self.use_model).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=4
        )
        ttk.Label(request, text="Model name (optional)").grid(row=2, column=2, sticky="e", padx=(8, 8), pady=4)
        model_entry = ttk.Entry(request, textvariable=self.model_name)
        model_entry.grid(row=2, column=3, sticky="ew", pady=4)
        self._bind_edit_guard(model_entry)

        ttk.Label(request, text="Box threshold").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=4)
        box_entry = ttk.Spinbox(request, from_=0.0, to=1.0, increment=0.05, textvariable=self.box_threshold, width=9)
        box_entry.grid(row=3, column=1, sticky="w", pady=4)
        self._bind_edit_guard(box_entry)
        ttk.Label(request, text="Text threshold").grid(row=3, column=2, sticky="e", padx=(8, 8), pady=4)
        text_entry = ttk.Spinbox(request, from_=0.0, to=1.0, increment=0.05, textvariable=self.text_threshold, width=9)
        text_entry.grid(row=3, column=3, sticky="w", pady=4)
        self._bind_edit_guard(text_entry)

        ttk.Label(request, text="Magnet position").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=4)
        magnet_entry = ttk.Spinbox(request, from_=0, to=999, textvariable=self.magnet_position, width=9)
        magnet_entry.grid(row=4, column=1, sticky="w", pady=4)
        self._bind_edit_guard(magnet_entry)

        ttk.Label(request, text="Workflow ID (optional)").grid(row=5, column=0, sticky="w", padx=(0, 8), pady=4)
        workflow_entry = ttk.Entry(request, textvariable=self.workflow_id, width=12)
        workflow_entry.grid(row=5, column=1, sticky="ew", pady=4)
        self._bind_edit_guard(workflow_entry)
        ttk.Label(request, text="Event ID (optional)").grid(row=5, column=2, sticky="e", padx=(8, 8), pady=4)
        event_entry = ttk.Entry(request, textvariable=self.workflow_event_id, width=12)
        event_entry.grid(row=5, column=3, sticky="ew", pady=4)
        self._bind_edit_guard(event_entry)

        self.run_button = ttk.Button(
            request,
            text="Detect, Reach, and Grab",
            style="Accent.TButton",
            command=lambda: self._dispatch(self.on_run),
        )
        self.run_button.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(14, 8))
        ttk.Label(request, textvariable=self.action_text, foreground="#5b6b79").grid(
            row=7, column=0, columnspan=4, sticky="w"
        )
        ttk.Label(request, textvariable=self.status_text, wraplength=560, justify="left").grid(
            row=8, column=0, columnspan=4, sticky="w", pady=(3, 0)
        )

        preview = ttk.LabelFrame(self, text="Detection photo", padding=10, style="Card.TLabelframe")
        preview.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 10))
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.photo_label = ttk.Label(preview, text="No reach-and-grab photo received yet", anchor="center", justify="center")
        self.photo_label.grid(row=0, column=0, sticky="nsew")

        results = ttk.LabelFrame(self, text="Vision and robot execution progress", padding=10, style="Card.TLabelframe")
        results.grid(row=1, column=0, columnspan=2, sticky="nsew")
        results.columnconfigure(0, weight=1)
        results.rowconfigure(0, weight=1)
        self.result_box = tk.Text(results, height=15, wrap="word", font=self._mono_font)
        self.result_box.grid(row=0, column=0, sticky="nsew")
        result_scroll = ttk.Scrollbar(results, orient="vertical", command=self.result_box.yview)
        result_scroll.grid(row=0, column=1, sticky="ns")
        self.result_box.configure(yscrollcommand=result_scroll.set, state="disabled")

    def set_result_text(self, text: str) -> None:
        self.result_box.configure(state="normal")
        self.result_box.delete("1.0", tk.END)
        self.result_box.insert(tk.END, text)
        self.result_box.configure(state="disabled")

    def show_photo_unavailable(self, message: str) -> None:
        self.photo_label.configure(text=message, image="")

    def show_photo(self, photo_image: Any) -> None:
        self._photo_image = photo_image
        self.photo_label.configure(image=photo_image, text="")

    def set_running(self, running: bool) -> None:
        self.run_button.configure(state="disabled" if running else "normal")

    @staticmethod
    def _dispatch(callback: Optional[Callable[[], None]]) -> None:
        if callback is not None:
            callback()
