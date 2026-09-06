"""JSON record files: the things that would be *lost* if deleted.

`settings.py` holds preferences — what the user chose, always safe to delete
because deleting it only resets defaults. This holds the other half: records.
An account, its topics, a calibration result. Delete this and something real
is gone, which is exactly why it is a separate file with a separate lifetime.

JSON rather than SQLite, for now. The persistence plan calls for a database
once there is telemetry to hold — thousands of rows, queried and filtered —
but an account list is a few dozen entries read once at startup and rewritten
whole on change. A text file the user can open, diff and back up is the right
weight for that, and moving to SQL later is a change behind this interface
rather than through every caller.

    Linux     ~/.local/share/DeskBuddy/Studio/<name>.json
    macOS     ~/Library/Application Support/DeskBuddy/Studio/<name>.json
    Windows   %LOCALAPPDATA%\\DeskBuddy\\Studio\\<name>.json

Qt picks the per-OS directory, the same way QSettings does for preferences, so
no path is hardcoded here either.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QStandardPaths

from .settings import APP, ORG


def data_dir() -> Path:
    """Where record files live. Created 0700 on first use.

    The org and app names are set here rather than assumed. QStandardPaths
    returns the bare application-data root without them — and once a
    QApplication exists Qt defaults applicationName() to the *script name*,
    which would scatter files into directories named after whatever launched
    the process. Setting both unconditionally keeps the path stable.
    """
    QCoreApplication.setOrganizationName(ORG)
    QCoreApplication.setApplicationName(APP)

    root = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    # These files carry credentials, so they are ours to read and nobody
    # else's — the same reasoning the broker directory is created 0700 under.
    path.chmod(0o700)
    return path


class Store:
    """One JSON file holding one kind of record.

    Read returns the default on anything unreadable — missing file, truncated
    write, hand-edited syntax error, a file written by a newer version with a
    shape this one does not understand. A record file that cannot be parsed
    must not stop the app from starting; the user loses those records, which
    is bad, but an app that will not open cannot even tell them that.
    """

    def __init__(self, name: str, *, directory: Path | None = None) -> None:
        self.name = name
        self._directory = directory

    @property
    def path(self) -> Path:
        directory = self._directory if self._directory is not None else data_dir()
        return directory / f"{self.name}.json"

    def read(self, default=None):
        path = self.path
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return default

    def write(self, data) -> None:
        """Replace the file's contents, atomically.

        Written to a temporary file in the same directory and renamed over the
        original: a crash or a full disk midway through leaves the previous
        file intact rather than a half-written one. os.replace is atomic on
        every platform we target, which a plain write is not.
        """
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)

        handle, temporary = tempfile.mkstemp(
            dir=path.parent, prefix=f".{self.name}-", suffix=".tmp"
        )
        try:
            with os.fdopen(handle, "w") as file:
                json.dump(data, file, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)
