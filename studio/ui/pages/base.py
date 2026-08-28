"""Base class every page inherits.

A Page owns its whole vertical slice and builds it top-down: the page invokes
its own contents, and those contents invoke theirs. Nothing about a page is
assembled behind its back.

Four things a page builds:

    build_actions()   the BAR 2 buttons
    build_side()      the side panel
    build_header()    title + subtitle  (rarely overridden)
    build_page()      the main body

`label` and `key` stay as attributes because the window needs them before the
page is built — that is the page's identity, not its content.

Not to be confused with a Service (PLAN.md), which is a background process
Studio starts and stops. Some pages drive a service; Logs and Manual do not.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

try:
    from ..components import ActionSpec, Separator, SidePanel
    from ..widgets import not_built_badge
except ImportError:
    from components import ActionSpec, Separator, SidePanel
    from widgets import not_built_badge


class Page:
    """One screen. Subclasses build their own contents."""

    # Identity. The window needs these before anything is built.
    key: str = ""
    label: str = ""

    # Shown in the header and the status strip.
    title: str = ""
    subtitle: str = ""
    status: str = ""

    def __init__(self) -> None:
        self._widget: QWidget | None = None
        self._side: QWidget | None = None

    # ---- BAR 2 -----------------------------------------------------------
    def build_actions(self) -> list:
        """The context-bar buttons for this page.

        Return ActionSpec(...) and Separator(). Override in every page.
        """
        return []

    # ---- side panel ------------------------------------------------------
    def build_side(self) -> QWidget | None:
        """The side panel. Return None for a page that has no side panel."""
        return None

    def side(self) -> QWidget | None:
        if self._side is None:
            self._side = self.build_side()
        return self._side

    # ---- main body -------------------------------------------------------
    def build_page(self) -> QWidget:
        """The body under the header. Override in every page."""
        raise NotImplementedError

    def build_header(self) -> QWidget:
        """Title + not-built badge + subtitle. Rarely overridden."""
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        row = QHBoxLayout()
        row.setSpacing(12)
        title = QLabel(self.title)
        title.setObjectName("Title")
        row.addWidget(title)
        row.addWidget(not_built_badge(), 0, Qt.AlignTop)
        row.addStretch(1)
        layout.addLayout(row)

        if self.subtitle:
            subtitle = QLabel(self.subtitle)
            subtitle.setObjectName("Subtitle")
            subtitle.setWordWrap(True)
            layout.addSpacing(10)
            layout.addWidget(subtitle)

        return holder

    # ---- assembly --------------------------------------------------------
    def widget(self) -> QWidget:
        """Header + body, scrollable. Built once, on first use."""
        if self._widget is not None:
            return self._widget

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(36, 30, 36, 30)
        layout.setSpacing(24)
        layout.addWidget(self.build_header())
        layout.addWidget(self.build_page())
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        self._widget = scroll
        return scroll
