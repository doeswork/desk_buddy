from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Optional


class VisualCalibrationView(ttk.Frame):
    """Widgets for the Visual Calibration tab.

    Builds layout only. PIL/ImageTk objects are handed in already built by
    the controller (this view has no opinion on whether Pillow is
    installed); it just holds the reference so Tk doesn't garbage-collect it.
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

        self.on_capture: Optional[Callable[[], None]] = None

        self.magnet_position = tk.IntVar(value=1)
        self.status_text = tk.StringVar(value="Not run yet")
        self._photo_image: Any = None

        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        instructions = ttk.LabelFrame(self, text="Visual AI calibration", padding=12, style="Card.TLabelframe")
        instructions.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 10))
        instructions.columnconfigure(1, weight=1)
        ttk.Label(
            instructions,
            text=(
                "Place the visual calibration target in the camera's working area. "
                "This sends calibrate_depth so firmware captures a fresh photo and "
                "the Vision server stores a new calibration grid."
            ),
            foreground="#5b6b79",
            wraplength=500,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Label(instructions, text="Magnet position").grid(row=1, column=0, sticky="w", padx=(0, 8))
        magnet_entry = ttk.Spinbox(instructions, from_=0, to=999, textvariable=self.magnet_position, width=10)
        magnet_entry.grid(row=1, column=1, sticky="w")
        self._bind_edit_guard(magnet_entry)
        ttk.Button(
            instructions,
            text="Capture Visual Calibration",
            style="Accent.TButton",
            command=lambda: self._dispatch(self.on_capture),
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 8))
        ttk.Label(instructions, textvariable=self.status_text, wraplength=500, justify="left").grid(
            row=3, column=0, columnspan=2, sticky="w"
        )

        preview = ttk.LabelFrame(self, text="Calibration photo", padding=10, style="Card.TLabelframe")
        preview.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 10))
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.photo_label = ttk.Label(
            preview, text="No visual calibration photo captured yet", anchor="center", justify="center"
        )
        self.photo_label.grid(row=0, column=0, sticky="nsew")

        results = ttk.LabelFrame(self, text="Vision server result", padding=10, style="Card.TLabelframe")
        results.grid(row=1, column=0, columnspan=2, sticky="nsew")
        results.columnconfigure(0, weight=1)
        results.rowconfigure(0, weight=1)
        self.result_box = tk.Text(results, height=14, wrap="word", font=self._mono_font)
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
        # Keep a reference: Tk does not retain PhotoImage data itself, and a
        # label with no other referrer would have its image garbage-collected.
        self._photo_image = photo_image
        self.photo_label.configure(image=photo_image, text="")

    @staticmethod
    def _dispatch(callback: Optional[Callable[[], None]]) -> None:
        if callback is not None:
            callback()
