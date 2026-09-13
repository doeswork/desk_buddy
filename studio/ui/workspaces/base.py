"""Base class every workspace inherits.

A Workspace is a top-level area of the app — Network, Vision, Workflows — and
it owns many pages. The workspace bar picks the workspace; the side panel is a list of
links into it; the page fills the body.

    Workspace          the workspace bar button, owns the pages and the shared state
      └── Page         one screen in the body, owns its actions and its body

Pages and the side panel are deliberately not the same list. `pages` is every
screen the workspace can show; `links` is what the panel offers. A page with
no link is still a page — Add Account is reached from a button on Accounts,
not from the panel, and would be noise in a list of places to go. Navigation
is by key throughout, so nothing depends on what order either list is in.

The toolbar belongs to the workspace, not to the page. Every Network page shows the
same Network buttons; what changes as you move around is which of them are
live, never which of them exist. A toolbar that reshuffles as you navigate is
one the user has to re-read every time, and a button that vanishes is one they
go looking for. Pages influence the bar only by what they make true — a
selected row, a password on screen — which `build_actions` reads.

The split between workspace and page is what a page may assume. A Page knows
its workspace and reads whatever it needs from it, so state fetched once — a
broker report, a model list — is fetched by the workspace and shared, not
re-fetched per page.

Not to be confused with a Service (PLAN.md), which is a background process
Studio starts and stops. A workspace may drive a service; the bottom debug
tray observes one without becoming a workspace of its own.
"""

from __future__ import annotations

from PySide6.QtWidgets import QStackedWidget, QWidget

from ..components import SidePanel


class Workspace:
    """One top-level area. Subclasses declare their pages."""

    # Identity. The workspace bar needs these before anything is built.
    key: str = ""
    label: str = ""

    # Every page this workspace can show. The first is where it opens.
    page_classes: list = []

    # The keys the side panel links to, in the order it lists them. Left empty
    # it links to every page; a workspace with unlisted pages names the ones
    # it wants shown.
    link_keys: list[str] = []

    def __init__(self) -> None:
        self.pages: list = [cls(self) for cls in self.page_classes]
        self._key = self.pages[0].key if self.pages else ""
        self._stack: QStackedWidget | None = None
        self._side: QWidget | None = None
        # Set by the window: lets a workspace whose state changed ask for the
        # chrome — the toolbar, the side panel, the status — to catch up with it.
        self.on_rebuilt = None
        # Also set by the window: say one transient line in the status strip.
        # For the outcome of an action the user just took, where the result
        # is worth confirming but not worth a permanent place on a page — a
        # connection that worked, a permission that was granted. A workspace
        # with no window attached simply says nothing.
        self.announce = None

    def say(self, message: str) -> None:
        """Put one line in the status strip, if there is a window to take it.

        Call *after* any refresh(): a rebuild repaints the status strip from
        the page's own `status`, so announcing first would be overwritten.
        """
        if self.announce is not None and message:
            self.announce(message)

    # ---- the current page ------------------------------------------------
    @property
    def page_key(self) -> str:
        """Which page is showing. Saved across sessions by the window."""
        return self._key

    @property
    def page(self):
        """The page showing right now. Everything the chrome reads comes from
        here — the workspace itself has no actions or status of its own."""
        return self.find(self._key) or self.pages[0]

    def find(self, key: str):
        """The page with this key, or None."""
        for page in self.pages:
            if page.key == key:
                return page
        return None

    def go_to(self, key: str) -> None:
        """Show a page, by key.

        Keys rather than indexes throughout: a page that wants Accounts should
        not have to know where Accounts sits, and stays right when a page is
        added, removed or reordered — including pages the panel never lists.
        """
        page = self.find(key)
        if page is None or key == self._key:
            return
        self._key = key
        # A page that is a step in a task starts fresh each time it is opened,
        # rather than showing whatever was left on it last time.
        if hasattr(page, "enter"):
            page.enter()
        if self._stack is not None:
            self._stack.setCurrentWidget(page.widget())
        self._sync_side()
        if self.on_rebuilt is not None:
            self.on_rebuilt()

    # ---- what the toolbar and the status strip read ----------------------------
    def build_actions(self) -> list:
        """This workspace's buttons — the same ones on all of its pages.

        Return ActionSpec(...) and Separator(). An action that cannot be taken
        right now keeps its place with `on_click=None`, which renders it
        disabled. Override in every workspace that has actions.
        """
        return []

    @property
    def status(self) -> str:
        """The status strip. The page decides it, since it is about what is
        on screen rather than about what the workspace can do."""
        return self.page.status

    # ---- the side panel: a list of links ---------------------------------
    def links(self) -> list[tuple[str, str]]:
        """What the panel offers, as (key, label) pairs.

        Defaults to every page. A workspace with pages that are reached from
        somewhere else names the ones it wants listed in `link_keys`.
        """
        if not self.link_keys:
            return [(page.key, page.label) for page in self.pages]
        found = [(key, page.label) for key in self.link_keys
                 if (page := self.find(key)) is not None]
        return found

    def side(self) -> QWidget | None:
        if self._side is None:
            self._side = self.build_side()
            self._sync_side()
        return self._side

    def build_side(self) -> QWidget | None:
        """The link list. Override for a panel that is not a list of links."""
        links = self.links()
        if len(links) < 2:
            return None
        panel = SidePanel(self.label, [label for _key, label in links],
                          enabled=True)
        keys = [key for key, _label in links]
        # The panel deals in rows; the workspace deals in keys. Translating
        # here is what keeps the two from having to agree on an order.
        panel.currentRowChanged.connect(
            lambda row: self.go_to(keys[row]) if 0 <= row < len(keys) else None
        )
        return panel

    def _sync_side(self) -> None:
        """Keep the panel's highlight on the page actually showing.

        A page can be reached without a click — `go_to`, or a restored session
        — and the row would otherwise point somewhere else. A page with no
        link clears the selection rather than leaving the last row lit, since
        none of them is where the user now is.
        """
        if self._side is None or not hasattr(self._side, "setCurrentRow"):
            return
        keys = [key for key, _label in self.links()]
        row = keys.index(self._key) if self._key in keys else -1
        self._side.blockSignals(True)
        self._side.setCurrentRow(row)
        self._side.blockSignals(False)

    def set_issue(self, key: str, issue: bool, detail: str = "") -> None:
        """Set a warning on a linked side-panel row, if one exists."""
        if self._side is None or not hasattr(self._side, "set_issue"):
            return
        keys = [link_key for link_key, _label in self.links()]
        if key in keys:
            self._side.set_issue(keys.index(key), issue, detail)

    # ---- assembly --------------------------------------------------------
    def widget(self) -> QWidget:
        """Every page, stacked. Built once, on first use."""
        if self._stack is not None:
            return self._stack

        self._stack = QStackedWidget()
        for page in self.pages:
            page.on_rebuilt = self._page_changed
            self._stack.addWidget(page.widget())
        self._stack.setCurrentWidget(self.page.widget())
        return self._stack

    def _page_changed(self) -> None:
        """A page rebuilt itself; the chrome above it may be out of date."""
        if self.on_rebuilt is not None:
            self.on_rebuilt()

    def refresh(self) -> None:
        """Rebuild every built page, after shared state changed.

        A workspace owns state its pages read, so a change to it is not one
        page's business — the Broker page starting the broker is what decides
        whether the Accounts page has anything to show.
        """
        for page in self.pages:
            page.rebuild()
        if self.on_rebuilt is not None:
            self.on_rebuilt()
