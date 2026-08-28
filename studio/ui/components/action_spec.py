"""What a page declares for one context-bar button.

Not a widget — a description the ContextBar turns into a ContextButton. Pages
return these from `build_actions()`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionSpec:
    label: str
    primary: bool = False
    enabled: bool = False


@dataclass(frozen=True)
class Separator:
    """A divider between groups of actions."""
