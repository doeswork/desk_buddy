"""User-config tests. Mostly about surviving a bad file.

    QT_QPA_PLATFORM=offscreen python -m studio.storage.tests

Preferences are read at startup before any window exists, so a settings file
that cannot be parsed must degrade to defaults rather than stop the app from
opening. Each case below is a file a user can actually end up with: hand-edited,
half-written by a crash, or synced from a different machine.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings

from . import keys
from .settings import Settings


def config_file(text: str) -> Settings:
    """A Settings backed by a throwaway file containing exactly `text`."""
    path = tempfile.mktemp(suffix=".ini")
    with open(path, "w") as handle:
        handle.write(text)
    return Settings(QSettings(path, QSettings.IniFormat))


def test_defaults_when_absent() -> None:
    s = config_file("")
    assert s.get(keys.THEME) == "light"
    assert s.get(keys.ZOOM_INDEX) == 2
    assert s.get(keys.LAST_PAGE) == ""


def test_round_trip() -> None:
    s = config_file("")
    s.set(keys.THEME, "dark")
    s.set(keys.ZOOM_INDEX, 5)
    s.sync()
    assert s.get(keys.THEME) == "dark"
    assert s.get(keys.ZOOM_INDEX) == 5


def test_types_survive_a_write_read_cycle() -> None:
    """The backends disagree about types; get() must normalise them."""
    s = config_file("")
    s.set(keys.ZOOM_INDEX, 4)
    s.sync()
    value = s.get(keys.ZOOM_INDEX)
    assert isinstance(value, int), f"expected int, got {type(value).__name__}"


def test_bad_int_falls_back_rather_than_zero() -> None:
    """QSettings' own type= coercion turns junk into 0, which for an index is
    a plausible-looking value that passes range checks. We must get the
    declared default instead."""
    s = config_file("[appearance]\nzoom_index=not-a-number\n")
    assert s.get(keys.ZOOM_INDEX) == 2, s.get(keys.ZOOM_INDEX)


def test_good_values_survive_a_bad_neighbour() -> None:
    s = config_file("[appearance]\ntheme=dark\nzoom_index=garbage\n")
    assert s.get(keys.THEME) == "dark"
    assert s.get(keys.ZOOM_INDEX) == 2


def test_truncated_file() -> None:
    s = config_file("[appearance\ntheme")
    assert s.get(keys.THEME) == "light"


def test_empty_value() -> None:
    s = config_file("[window]\nlast_page=\n")
    assert s.get(keys.LAST_PAGE) == ""


def test_bytes_key_tolerates_junk() -> None:
    s = config_file("[window]\ngeometry=notbytes\n")
    assert isinstance(s.get(keys.GEOMETRY), bytes)


def test_clear_resets_to_defaults() -> None:
    s = config_file("")
    s.set(keys.THEME, "dark")
    s.clear()
    assert s.get(keys.THEME) == "light"


def test_every_key_reads_as_its_declared_type() -> None:
    """A key whose default does not match its own type is a bug in keys.py."""
    s = config_file("")
    for key in keys.ALL:
        value = s.get(key)
        assert isinstance(value, key.type), (
            f"{key.name}: default is {type(value).__name__}, declared {key.type.__name__}"
        )


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} settings tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
