"""The four workspaces, in the workspace bar order.

A workspace is a top-level area and owns many pages; the workspace bar switches between
workspaces, the side panel switches between one workspace's pages.

To add a workspace: write the file, import it, add it to WORKSPACE_CLASSES.
To add a page to one: write the class, add it to that workspace's
`page_classes`. Nothing else in the app needs to change.
"""

from __future__ import annotations

from .base import Workspace
from .calibration import CalibrationWorkspace
from .network import NetworkWorkspace
from .vision import VisionWorkspace
from .workflows import WorkflowsWorkspace

WORKSPACE_CLASSES = [
    NetworkWorkspace,
    CalibrationWorkspace,
    WorkflowsWorkspace,
    VisionWorkspace,
]


def build_workspaces() -> list[Workspace]:
    return [cls() for cls in WORKSPACE_CLASSES]


__all__ = ["Workspace", "WORKSPACE_CLASSES", "build_workspaces"]
