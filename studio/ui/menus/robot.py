"""Robot menu — connection and firmware."""

from __future__ import annotations

try:
    from .base import SEPARATOR, Menu
except ImportError:
    from base import SEPARATOR, Menu


class RobotMenu(Menu):
    title = "Robot"

    entries = [
        ("Connect…", ""),
        ("Disconnect", ""),
        SEPARATOR,
        ("Reboot", ""),
        ("Firmware Update…", ""),
    ]
