"""What a page declares for one context-bar button.

Not a widget — a description the Toolbar turns into a ToolbarButton. Pages
return these from `build_actions()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ActionSpec:
    label: str
    primary: bool = False
    enabled: bool = False
    # What the button does. An action with a handler enables itself — a button
    # that does something and a button that does not should not be able to
    # disagree about whether it is clickable.
    on_click: Callable[[], None] | None = None

    @property
    def clickable(self) -> bool:
        return self.enabled or self.on_click is not None


@dataclass(frozen=True)
class Separator:
    """A divider between groups of actions."""
