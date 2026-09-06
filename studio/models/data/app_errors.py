"""Unhandled application errors persisted for the debug tray."""

from __future__ import annotations

import traceback as traceback_module
from dataclasses import dataclass
from datetime import datetime, timezone
from types import TracebackType

from .database import Database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class AppError:
    id: int
    occurred_at: str
    source: str
    exception_type: str
    message: str
    traceback: str

    @property
    def heading(self) -> str:
        try:
            stamp = datetime.fromisoformat(self.occurred_at).astimezone()
            time_text = stamp.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            time_text = self.occurred_at
        detail = f": {self.message}" if self.message else ""
        return f"[{time_text}] {self.source}  {self.exception_type}{detail}"

    @property
    def display(self) -> str:
        trace = self.traceback.strip()
        return f"{self.heading}\n{trace}" if trace else self.heading


class AppErrors:
    """Append and query error history without exposing SQL to the UI."""

    def __init__(self, database: Database | None = None) -> None:
        self.database = database if database is not None else Database()

    @property
    def path(self):
        return self.database.path

    def append(
        self,
        exception_type: str,
        message: str,
        traceback: str = "",
        *,
        source: str = "application",
        occurred_at: str | None = None,
    ) -> AppError:
        stamp = occurred_at or _now()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO app_error
                    (occurred_at, source, exception_type, message, traceback)
                VALUES (?, ?, ?, ?, ?)
                """,
                (stamp, source, exception_type, message, traceback),
            )
            error_id = int(cursor.lastrowid)
        return AppError(
            error_id, stamp, source, exception_type, message, traceback
        )

    def record_exception(
        self,
        exception_type: type[BaseException],
        exception: BaseException,
        traceback: TracebackType | None,
        *,
        source: str = "application",
    ) -> AppError:
        formatted = "".join(
            traceback_module.format_exception(exception_type, exception, traceback)
        )
        return self.append(
            exception_type.__name__,
            str(exception),
            formatted,
            source=source,
        )

    def recent(self, limit: int = 500) -> list[AppError]:
        limit = max(0, int(limit))
        if not limit:
            return []
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, occurred_at, source, exception_type, message, traceback
                FROM app_error
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._from_row(row) for row in reversed(rows)]

    def count(self) -> int:
        with self.database.connect() as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM app_error").fetchone()[0]
            )

    def latest_id(self) -> int:
        with self.database.connect() as connection:
            return int(
                connection.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM app_error"
                ).fetchone()[0]
            )

    def clear(self) -> None:
        with self.database.connect() as connection:
            connection.execute("DELETE FROM app_error")

    @staticmethod
    def _from_row(row) -> AppError:
        return AppError(
            id=row["id"],
            occurred_at=row["occurred_at"],
            source=row["source"],
            exception_type=row["exception_type"],
            message=row["message"],
            traceback=row["traceback"],
        )


_instance: AppErrors | None = None


def app_errors() -> AppErrors:
    global _instance
    if _instance is None:
        _instance = AppErrors()
    return _instance
