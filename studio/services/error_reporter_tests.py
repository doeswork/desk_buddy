"""Checks for process-wide exception capture using throwaway SQLite."""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

from ..models.data.app_errors import AppErrors
from ..models.data.database import Database
from .error_reporter import ErrorReporter


def fresh_reporter() -> tuple[ErrorReporter, AppErrors]:
    path = Path(tempfile.mkdtemp()) / "studio.sqlite3"
    errors = AppErrors(Database(path))
    return ErrorReporter(errors), errors


def test_an_exception_is_persisted_without_becoming_a_second_error() -> None:
    reporter, errors = fresh_reporter()
    try:
        raise LookupError("missing robot")
    except LookupError as exception:
        reporter._record(
            type(exception),
            exception,
            exception.__traceback__,
            source="application",
        )

    saved = errors.recent()[0]
    assert saved.exception_type == "LookupError"
    assert saved.message == "missing robot"
    assert "raise LookupError" in saved.traceback


def test_hooks_install_and_restore_exactly_what_was_there() -> None:
    reporter, _errors = fresh_reporter()
    previous_sys = sys.excepthook
    previous_thread = threading.excepthook
    reporter.install()
    try:
        assert sys.excepthook == reporter._sys_hook
        assert threading.excepthook == reporter._thread_hook
        reporter.install()  # Idempotent; this must not wrap itself.
    finally:
        reporter.uninstall()
    assert sys.excepthook is previous_sys
    assert threading.excepthook is previous_thread


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} error reporter tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
