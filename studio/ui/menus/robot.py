"""Robot menu — connection and firmware."""

from __future__ import annotations

from .base import SEPARATOR, Menu


class RobotMenu(Menu):
    title = "Robot"

    entries = [
        ("Connect…", ""),
        ("Disconnect", ""),
        SEPARATOR,
        ("Reboot", ""),
        ("Firmware Update…", ""),
    ]
