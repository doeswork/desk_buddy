"""Base class every page inherits.

A Page is one screen inside a workspace. It owns its own vertical slice and
builds it top-down: the page invokes its own contents, and those contents
invoke theirs. Nothing about a page is assembled behind its back.

Three things a page builds:

    build_header()          title + subtitle, rarely overridden
    build_header_actions()  the header's right-hand slot, optional
    build_page()             the main body

Neither the side panel nor the toolbar is one of them — both belong to the workspace
(see workspaces/base.py), so they keep their shape as the user moves between
pages. A page-level action that is not that — one button naming the page's
one verb, like Accounts' Add Account — belongs in `build_header_actions`
instead: it sits at the header's right edge, level with the subtitle, rather
than floating as its own block above the body. A page that needs more than
that, or a list, still puts it in its own body.

`label` and `key` stay as attributes because the side panel needs them before
the page is built — that is the page's identity, not its content.
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

    def build_header_actions(self) -> QWidget | None:
        """The header's right-hand slot. None for a page with nothing there.

        For the one button that names what this whole page is for — Add
        Account, say. Anything that needs more room than that, or that acts
        on a specific row rather than the page as a whole, belongs in the
        body instead.
        """
        return None

    def build_header(self) -> QWidget:
        """Title + subtitle on the left, header actions on the right.

        Rarely overridden — override build_header_actions() instead, which
        keeps the alignment (actions level with the subtitle column, not the
        title alone) in one place for every page.
        """
        text = QWidget()
        text_layout = QVBoxLayout(text)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(0)

        title = QLabel(self.title)
        title.setObjectName("Title")
        text_layout.addWidget(title)

        if self.subtitle:
            subtitle = QLabel(self.subtitle)
            subtitle.setObjectName("Subtitle")
            subtitle.setWordWrap(True)
            text_layout.addSpacing(HEADER_SPACING)
            text_layout.addWidget(subtitle)

        actions = self.build_header_actions()
        if actions is None:
            return text

        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(text, 1)
        layout.addWidget(actions, 0, Qt.AlignBottom)
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
        """Rebuild the header and body in place, after this page's state
        changed.

        The scroll area itself stays, so the page does not flicker or lose
        its scroll position. Both the header and the body are replaced,
        because build_header_actions() depends on the same state build_page()
        does — Accounts' Add Account button has to come and go with whether
        there is a password on screen, same as the table beneath it. A page
        that never changes never calls this.
        """
        if self._widget is None:
            return  # Never built; the next widget() call is already current.

        inner = self._widget.widget()
        layout = inner.layout()

        # Header and body sit before the trailing stretch, in that order.
        for index in (0, 1):
            item = layout.takeAt(0)
            if item is not None and item.widget() is not None:
                old = item.widget()
                # Unparent before deleting. takeAt() only drops it from the
                # layout: it stays a child of `inner` until the deferred
                # delete runs, and goes on painting at its old geometry
                # underneath the replacement in the meantime.
                old.setParent(None)
                old.deleteLater()
        layout.insertWidget(0, self.build_header())
        layout.insertWidget(1, self.build_page())

        if self.on_rebuilt is not None:
            self.on_rebuilt()
