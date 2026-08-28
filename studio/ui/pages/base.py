"""Base class every page inherits.

A Page is one screen: its BAR 1 label, its BAR 2 actions, its side panel, and
its body. Adding a page means adding one file and one line in
`pages/__init__.py` — nothing else in the app changes.

Not to be confused with a Service (PLAN.md), which is a background process
Studio starts and stops. Some pages drive a service; Logs and Manual do not.

Subclasses set the class attributes and override `build_page()`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

try:
    from ..widgets import not_built_badge
except ImportError:
    from widgets import not_built_badge

# Put this in an actions list to get a divider on the context bar.
SEPARATOR = "---"


class Page:
    # --- BAR 1 ---
    key: str = ""
    label: str = ""

    # --- BAR 2. Strings become disabled buttons; SEPARATOR becomes a divider. ---
    actions: list[str] = []

    # --- side dock ---
    side_title: str = ""
    side_items: list[str] = []

    # --- main page ---
    title: str = ""
    subtitle: str = ""
    status: str = ""

    def __init__(self) -> None:
        self._page: QWidget | None = None

    # ---- BAR 2 -----------------------------------------------------------
    def build_actions(self, parent: QWidget) -> list[QAction | None]:
        """None marks a separator. Everything is disabled until it works."""
        built: list[QAction | None] = []
        for label in self.actions:
            if label == SEPARATOR:
                built.append(None)
                continue
            action = QAction(label, parent)
            action.setEnabled(False)
            built.append(action)
        return built

    # ---- main page -------------------------------------------------------
    def page(self) -> QWidget:
        if self._page is None:
            self._page = self._wrap(self.build_page())
        return self._page

    def build_page(self) -> QWidget:
        """Override. Return the body that sits under the headline."""
        raise NotImplementedError

    def _wrap(self, body: QWidget) -> QWidget:
        """Headline + subtitle + the subclass's body, in a scroll area."""
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(36, 30, 36, 30)
        layout.setSpacing(20)

        header = QHBoxLayout()
        header.setSpacing(12)
        title = QLabel(self.title)
        title.setObjectName("Title")
        header.addWidget(title)
        header.addWidget(not_built_badge(), 0, Qt.AlignTop)
        header.addStretch(1)
        layout.addLayout(header)

        subtitle = QLabel(self.subtitle)
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        layout.addSpacing(4)

        layout.addWidget(body)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        return scroll


def stack(*widgets: QWidget) -> QWidget:
    """Vertical stack of cards. What most build_page() bodies are."""
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(16)
    for widget in widgets:
        layout.addWidget(widget)
    return holder
