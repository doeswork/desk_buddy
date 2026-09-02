"""Every setting the app persists, declared once.

A key is a name, a type, and a default. Nothing else in the app may invent a
settings key inline: a typo in a raw string is a silently-lost preference that
nobody notices until a user reports "it forgets my theme", whereas a typo here
is an ImportError at startup.

The type matters more than it looks. QSettings stores values as text, and the
backends disagree about what comes back — a float written on Linux may return
as the string "1.3" on macOS. Declaring the type lets Settings.get() coerce on
read, so callers always get what they expect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Key:
    name: str          # the "section/key" path written to the file
    type: type         # what get() coerces to
    default: Any       # used on first run, and whenever the stored value is junk


# ---- Appearance ---------------------------------------------------------
THEME = Key("appearance/theme", str, "light")
ZOOM_INDEX = Key("appearance/zoom_index", int, 2)   # index into ZOOM_LEVELS

# ---- Window -------------------------------------------------------------
# Qt serialises these itself; we only carry the bytes.
GEOMETRY = Key("window/geometry", bytes, b"")
WINDOW_STATE = Key("window/state", bytes, b"")
LAST_PAGE = Key("window/last_page", int, 0)

ALL = (THEME, ZOOM_INDEX, GEOMETRY, WINDOW_STATE, LAST_PAGE)
