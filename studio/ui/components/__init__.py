"""Reusable UI components.

One widget per file. A component owns its own object name; the matching style
lives in theme/ so the whole look stays in one place.

Pages compose these top-down: a page builds its contents, and those contents
build theirs.
"""

from __future__ import annotations

from .action_spec import ActionSpec, Separator
from .card import Card
from .command_card import CommandCard
from .steps_card import StepsCard
from .column import Column
from .context_bar import ContextBar, ContextButton
from .debug_tray import DebugTray
from .nav_bar import NavBar
from .preferences_dialog import PreferencesDialog
from .serial_monitor import SerialMonitor
from .side_panel import SidePanel
from .spacer import spacer

__all__ = [
    "ActionSpec",
    "Card",
    "CommandCard",
    "StepsCard",
    "Column",
    "ContextBar",
    "ContextButton",
    "DebugTray",
    "NavBar",
    "PreferencesDialog",
    "SerialMonitor",
    "Separator",
    "SidePanel",
    "spacer",
]
