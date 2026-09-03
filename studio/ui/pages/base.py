"""Base class every page inherits.

A Page is one screen inside a workspace. It owns its own vertical slice and
builds it top-down: the page invokes its own contents, and those contents
invoke theirs. Nothing about a page is assembled behind its back.

Two things a page builds:

    build_header()    title + subtitle  (rarely overridden)
    build_page()      the main body

Neither the side panel nor BAR 2 is one of them — both belong to the workspace
(see workspaces/base.py), so they keep their shape as the user moves between
pages. A page that needs a list, or a button that acts on the one form it is
showing, puts it in its own body where it belongs.

`label` and `key` stay as attributes because the side panel needs them before
the page is built — that is the page's identity, not its content.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..theme.metrics import (
    HEADER_SPACING,
    PAGE_MARGIN_H,
    PAGE_MARGIN_V,
    PAGE_SPACING,
)


class Page:
    """One screen. Subclasses build their own contents."""

    # Identity. The side panel needs these before anything is built.
    key: str = ""
    label: str = ""

    # Shown in the header and the status strip.
    title: str = ""
    subtitle: str = ""
    status: str = ""

    def __init__(self, workspace=None) -> None:
        # The workspace this page belongs to. Shared state — a broker report,
        # an installed-model list — is read from here rather than fetched
        # again per page.
        self.workspace = workspace
        self._widget: QWidget | None = None
        # Set by the workspace: lets a page whose state changed ask for the
        # context bar and status strip to catch up with it.
        self.on_rebuilt = None

    # ---- main body -------------------------------------------------------
    def build_page(self) -> QWidget:
        """The body under the header. Override in every page."""
        raise NotImplementedError

    def build_header(self) -> QWidget:
        """Title + subtitle. Rarely overridden."""
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title = QLabel(self.title)
        title.setObjectName("Title")
        layout.addWidget(title)

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
            old = item.widget()
            # Unparent before deleting. takeAt() only drops it from the
            # layout: it stays a child of `inner` until the deferred delete
            # runs, and goes on painting at its old geometry underneath the
            # replacement in the meantime.
            old.setParent(None)
            old.deleteLater()
        layout.insertWidget(1, self.build_page())

        if self.on_rebuilt is not None:
            self.on_rebuilt()
