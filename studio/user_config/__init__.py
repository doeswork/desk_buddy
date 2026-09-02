"""User preferences — things the user chose.

    keys.py       every setting, declared once with a type and a default
    settings.py   the INI file, one strategy on all three platforms

Everything here must stay safe to delete: doing so resets the app to defaults
and loses nothing. Anything that would be *lost* by deleting it is records, not
preferences, and belongs in `studio/database/` instead — see persistence_plan
in PLAN.md for the split.
"""

from __future__ import annotations

from . import keys
from .settings import Settings, settings

__all__ = ["Settings", "keys", "settings"]
