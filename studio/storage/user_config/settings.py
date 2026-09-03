"""User preferences. One strategy on Windows, macOS, and Linux.

Qt's QSettings already solves the hard part — *where* a preferences file belongs
on each OS — so we don't hardcode paths. What we do override is the *format*.

By default QSettings uses each platform's native backend: the registry on
Windows, a .plist on macOS, an INI file on Linux. That is three storage engines
with three sets of quirks, and on Windows it means a user's preferences live
somewhere they cannot open, diff, back up, or delete. Forcing IniFormat gives
one readable text file everywhere, at a per-OS path Qt still chooses for us:

    Linux     ~/.config/DeskBuddy/Studio.ini
    macOS     ~/Library/Preferences/DeskBuddy/Studio.ini
    Windows   %APPDATA%\\DeskBuddy\\Studio.ini

This file holds preferences only — things the user chose. It must always be
safe to delete: doing so resets the app to defaults and loses nothing. Anything
that would be *lost* by deleting it belongs in the database instead.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings

from . import keys
from .keys import Key

ORG = "DeskBuddy"
APP = "Studio"


class Settings:
    """Typed access to the preferences file."""

    def __init__(self, backend: QSettings | None = None) -> None:
        self._q = backend or QSettings(
            QSettings.IniFormat, QSettings.UserScope, ORG, APP
        )

    @property
    def path(self) -> str:
        """Where this is actually stored. Worth showing in a bug report."""
        return self._q.fileName()

    def get(self, key: Key):
        """Read a value, coerced to the key's declared type.

        A settings file can be hand-edited, written by a newer version of the
        app, or truncated by a bad shutdown. Any value we cannot read as the
        declared type is treated as absent, so a corrupt file degrades to
        defaults instead of crashing at startup.

        We coerce from the raw value rather than using QSettings' own `type=`
        argument, because that silently converts junk to a zero value instead
        of falling back: `value("k", 2, type=int)` on the text "not-a-number"
        returns 0, not 2 — which for an index is a *valid* answer and so
        survives any range check the caller makes.
        """
        if not self._q.contains(key.name):
            return key.default

        raw = self._q.value(key.name)
        if raw is None:
            return key.default
        if isinstance(raw, key.type):
            return raw

        try:
            if key.type is bool:
                return str(raw).strip().lower() in ("true", "1", "yes", "on")
            if key.type is bytes:
                return bytes(raw)
            return key.type(raw)
        except (TypeError, ValueError):
            return key.default

    def set(self, key: Key, value) -> None:
        self._q.setValue(key.name, value)

    def sync(self) -> None:
        """Flush to disk. Qt also does this periodically and at exit."""
        self._q.sync()

    def clear(self) -> None:
        """Reset every preference to its default."""
        self._q.clear()
        self._q.sync()


# The app-wide instance. Built lazily so importing this module never touches
# disk — tests and tools can construct their own Settings(backend=...) instead.
_instance: Settings | None = None


def settings() -> Settings:
    global _instance
    if _instance is None:
        _instance = Settings()
    return _instance
