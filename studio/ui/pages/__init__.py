"""The six pages, in BAR 1 order.

To add a page: write the file, import it, add it to PAGE_CLASSES. Nothing else
in the app needs to change.
"""

from __future__ import annotations

from .base import SEPARATOR, Page
from .calibration import CalibrationPage
from .logs import LogsPage
from .manual import ManualPage
from .messages import MessagesPage
from .vision import VisionPage
from .workflows import WorkflowsPage

PAGE_CLASSES = [
    MessagesPage,
    VisionPage,
    WorkflowsPage,
    CalibrationPage,
    LogsPage,
    ManualPage,
]


def build_pages() -> list[Page]:
    return [cls() for cls in PAGE_CLASSES]


__all__ = ["SEPARATOR", "Page", "PAGE_CLASSES", "build_pages"]
