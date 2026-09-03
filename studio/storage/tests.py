"""Storage tests. Mostly about surviving a bad file.

    QT_QPA_PLATFORM=offscreen python -m studio.storage.tests

Both halves of storage are here: preferences (settings.py) and record files
(store.py). Both are read at startup before any window exists, so a file that
cannot be parsed must degrade to defaults rather than stop the app from
opening. Each case below is a file a user can actually end up with:
hand-edited, half-written by a crash, or synced from a different machine.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings

from . import keys
from .settings import Settings
from .store import Store


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


# ---- The record store ----------------------------------------------------

def fresh_store(name: str = "records") -> Store:
    return Store(name, directory=Path(tempfile.mkdtemp()))


def test_a_record_file_round_trips() -> None:
    store = fresh_store()
    assert store.read(default="fallback") == "fallback", "missing file"

    store.write([{"name": "black"}, {"name": "silver"}])
    assert store.read() == [{"name": "black"}, {"name": "silver"}]


def test_an_unreadable_file_falls_back() -> None:
    """A record file must never be the reason the app will not start."""
    store = fresh_store()
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ not json at all")
    assert store.read(default=[]) == []


def test_a_record_file_is_written_atomically() -> None:
    """A failed write leaves the previous contents, not a half-written file."""
    store = fresh_store()
    store.write({"keep": "this"})

    class Unserialisable:
        pass

    try:
        store.write({"bad": Unserialisable()})
    except TypeError:
        pass
    else:
        raise AssertionError("expected the bad write to fail")

    assert store.read() == {"keep": "this"}
    leftovers = [p for p in store.path.parent.iterdir() if p.suffix == ".tmp"]
    assert not leftovers, leftovers


def test_a_record_file_is_private() -> None:
    """These hold credentials, so they are ours to read and nobody else's."""
    store = fresh_store()
    store.write(["anything"])
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_deleting_a_record_file_is_safe() -> None:
    store = fresh_store()
    store.delete()          # never written; must not raise
    store.write(["x"])
    store.delete()
    assert not store.path.exists()


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} storage tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
