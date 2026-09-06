"""Studio's SQLite file and forward-only schema migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ...storage.store import data_dir

SCHEMA_VERSION = 2


class Database:
    """Open short-lived, thread-safe connections to one SQLite database.

    MQTT callbacks run outside Qt's UI thread. A connection per operation is
    intentionally cheap here and, unlike sharing one connection, is safe from
    either thread. WAL lets the tray read while a message is being inserted.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else data_dir() / "studio.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _migrate(self) -> None:
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema {version} is newer than this Studio "
                    f"supports ({SCHEMA_VERSION})."
                )
            if version < 1:
                connection.executescript(
                    """
                    PRAGMA journal_mode = WAL;
                    CREATE TABLE mqtt_message (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        received_at TEXT NOT NULL,
                        topic       TEXT NOT NULL,
                        payload     BLOB NOT NULL,
                        qos         INTEGER NOT NULL DEFAULT 0,
                        retained    INTEGER NOT NULL DEFAULT 0,
                        heartbeat   INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE INDEX mqtt_message_received_at
                        ON mqtt_message(received_at);
                    CREATE INDEX mqtt_message_heartbeat_id
                        ON mqtt_message(heartbeat, id);
                    PRAGMA user_version = 1;
                    """
                )
                version = 1
            if version < 2:
                connection.executescript(
                    """
                    CREATE TABLE app_error (
                        id             INTEGER PRIMARY KEY AUTOINCREMENT,
                        occurred_at    TEXT NOT NULL,
                        source         TEXT NOT NULL DEFAULT 'application',
                        exception_type TEXT NOT NULL,
                        message        TEXT NOT NULL,
                        traceback      TEXT NOT NULL
                    );
                    CREATE INDEX app_error_occurred_at
                        ON app_error(occurred_at);
                    PRAGMA user_version = 2;
                    """
                )
        self.path.chmod(0o600)
