"""Help menu — docs and about."""

from __future__ import annotations

try:
    from .base import SEPARATOR, Menu
except ImportError:
    from base import SEPARATOR, Menu


class HelpMenu(Menu):
    title = "Help"

    entries = [
        ("Build Guide", ""),
        ("MQTT Spec", ""),
        SEPARATOR,
        ("About Desk Buddy Studio", ""),
    ]
