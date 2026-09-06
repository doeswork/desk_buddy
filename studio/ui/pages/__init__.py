"""The Page base class.

Pages themselves live with the workspace that owns them, in ui/workspaces/ —
a page has no meaning apart from its workspace, so it is not registered here.
"""

from __future__ import annotations

from .base import Page

__all__ = ["Page"]
