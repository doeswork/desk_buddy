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

from ..components import ActionSpec, Separator, SidePanel
from ..theme.metrics import (
    HEADER_GAP,
    HEADER_SPACING,
    PAGE_MARGIN_H,
    PAGE_MARGIN_V,
    PAGE_SPACING,
)
from ..widgets import not_built_badge


class Page:
    """One screen. Subclasses build their own contents."""

    # Identity. The window needs these before anything is built.
    key: str = ""
    label: str = ""

    # Shown in the header and the status strip.
    title: str = ""
    subtitle: str = ""
    status: str = ""
    built: bool = False

    def __init__(self) -> None:
        self._widget: QWidget | None = None
        self._side: QWidget | None = None
        # Set by the window: lets a page whose state changed ask for the
        # context bar and status strip to catch up with it.
        self.on_rebuilt = None

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
        row.setSpacing(HEADER_GAP)
        title = QLabel(self.title)
        title.setObjectName("Title")
        row.addWidget(title)
        if not self.built:
            row.addWidget(not_built_badge(), 0, Qt.AlignTop)
        row.addStretch(1)
        layout.addLayout(row)

        if self.subtitle:
            subtitle = QLabel(self.subtitle)
            subtitle.setObjectName("Subtitle")
            subtitle.setWordWrap(True)
            layout.addSpacing(HEADER_SPACING)
            layout.addWidget(subtitle)

        return holder

    # ---- assembly --------------------------------------------------------
    def widget(self) -> QWidget:
        """Header + body, scrollable. Built once, on first use."""
        if self._widget is not None:
            return self._widget

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(
            PAGE_MARGIN_H, PAGE_MARGIN_V, PAGE_MARGIN_H, PAGE_MARGIN_V
        )
        layout.setSpacing(PAGE_SPACING)
        layout.addWidget(self.build_header())
        layout.addWidget(self.build_page())
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        self._widget = scroll
        return scroll

    def rebuild(self) -> None:
        """Rebuild the body in place, after this page's state changed.

        The header and the scroll area stay; only the section stack is
        replaced, so the page does not flicker or lose its scroll position.
        A page that never changes never calls this.
        """
        if self._widget is None:
            return  # Never built; the next widget() call is already current.

        inner = self._widget.widget()
        layout = inner.layout()

        # The body sits between the header and the trailing stretch.
        item = layout.takeAt(1)
        if item is not None and item.widget() is not None:
            item.widget().deleteLater()
        layout.insertWidget(1, self.build_page())

        if self.on_rebuilt is not None:
            self.on_rebuilt()
