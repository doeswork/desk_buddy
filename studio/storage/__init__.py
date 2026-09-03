"""Where Studio keeps things on disk. Two halves, on purpose.

    keys.py       every setting, declared once with a type and a default
    settings.py   preferences: the INI file, one strategy on all platforms
    store.py      records: one JSON file per kind, read and written whole

The split is about what deleting a file costs. Preferences are things the user
chose — a theme, a window size — and deleting them resets the app to defaults
and loses nothing. Records are things the user *made*: an account and its
password, a calibration result. Delete those and something real is gone.

Models own what a record means and when it changes (`studio/models/`); this
layer only knows how to put one on disk and get it back. Records will move to
SQLite once there is telemetry to hold — see persistence_plan in PLAN.md —
which is a change behind Store, not through every caller.
"""

from __future__ import annotations

from . import keys
from .settings import Settings, settings
from .store import Store, data_dir

__all__ = ["Settings", "Store", "data_dir", "keys", "settings"]
