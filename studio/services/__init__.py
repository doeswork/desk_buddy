"""Services. No Qt in here.

Each service is its own subpackage; `network` is one of them.
"""

from __future__ import annotations

from .error_reporter import ErrorReporter

__all__ = ["ErrorReporter"]
