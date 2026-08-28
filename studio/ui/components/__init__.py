"""Reusable UI components.

One widget per file. A component owns its own object name; the matching style
lives in theme.py so the whole look stays in one place.

Pages compose these top-down: a page builds its contents, and those contents
build theirs.
"""

from __future__ import annotations

from .action_spec import ActionSpec, Separator
from .card import Card
from .column import Column
from .context_bar import ContextBar, ContextButton
from .nav_bar import NavBar
from .side_panel import SidePanel
from .spacer import spacer

__all__ = [
    "ActionSpec",
    "Card",
    "Column",
    "ContextBar",
    "ContextButton",
    "NavBar",
    "Separator",
    "SidePanel",
    "spacer",
]
