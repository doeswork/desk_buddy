"""Persist uncaught Python exceptions without suppressing normal reporting."""

from __future__ import annotations

import sys
import threading

from ..models.data.app_errors import AppErrors


class ErrorReporter:
    """Install process-wide hooks for main and worker thread failures."""

    def __init__(self, errors: AppErrors) -> None:
        self.errors = errors
        self._installed = False
        self._previous_sys = None
        self._previous_thread = None

    def install(self) -> None:
        if self._installed:
            return
        self._previous_sys = sys.excepthook
        self._previous_thread = threading.excepthook
        sys.excepthook = self._sys_hook
        threading.excepthook = self._thread_hook
        self._installed = True

    def uninstall(self) -> None:
        if not self._installed:
            return
        sys.excepthook = self._previous_sys or sys.__excepthook__
        threading.excepthook = (
            self._previous_thread or threading.__excepthook__
        )
        self._installed = False

    def _sys_hook(self, exception_type, exception, traceback) -> None:
        self._record(exception_type, exception, traceback, source="application")
        if self._previous_sys is not None:
            self._previous_sys(exception_type, exception, traceback)

    def _thread_hook(self, args) -> None:
        name = args.thread.name if args.thread is not None else "unknown"
        self._record(
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
            source=f"thread:{name}",
        )
        if self._previous_thread is not None:
            self._previous_thread(args)

    def _record(self, exception_type, exception, traceback, *, source: str) -> None:
        try:
            self.errors.record_exception(
                exception_type, exception, traceback, source=source
            )
        except BaseException:
            # Reporting an error must never replace the original exception
            # with a secondary database failure.
            pass
