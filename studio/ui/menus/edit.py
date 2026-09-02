"""Edit menu — undo stack and clipboard."""

from __future__ import annotations

from .base import SEPARATOR, Menu


class EditMenu(Menu):
    title = "Edit"

    entries = [
        ("Undo", "Ctrl+Z"),
        ("Redo", "Ctrl+Shift+Z"),
        SEPARATOR,
        ("Cut", "Ctrl+X"),
        ("Copy", "Ctrl+C"),
        ("Paste", "Ctrl+V"),
    ]
