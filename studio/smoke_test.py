"""Headless check that the UI builds. Runs in CI before the slow bundle step.

    QT_QPA_PLATFORM=offscreen python -m studio.smoke_test
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from .storage import keys
from .storage.settings import Settings
from .ui.components import ActionSpec
from .ui.main_window import MainWindow
from .ui.menus.view import DEFAULT_ZOOM_INDEX


def main() -> int:
    app = QApplication([])
    window = MainWindow()
    window.show()

    assert window.windowTitle() == "Desk Buddy Studio"

    workspaces = window.workspaces
    assert len(workspaces) == 6, f"expected 6 workspaces, got {len(workspaces)}"
    assert window.stack.count() == len(workspaces), "workspace missing"

    total_pages = 0
    for index, workspace in enumerate(workspaces):
        window.select_workspace(index)

        assert window.stack.currentIndex() == index, (
            f"{workspace.key}: workspace did not switch"
        )
        assert window.nav_bar.actions_by_index[index].isChecked(), (
            f"{workspace.key}: not checked"
        )

        # The panel lists links, not pages: it shows what the workspace chose
        # to link to, and disappears when there is nothing to choose between.
        links = workspace.links()
        side = workspace.side()
        if len(links) > 1:
            assert side is not None, f"{workspace.key}: no side panel"
            assert side.count() == len(links), (
                f"{workspace.key}: panel rows {side.count()} != links {len(links)}"
            )
        for key, _label in links:
            assert workspace.find(key) is not None, (
                f"{workspace.key}: link {key!r} points at no page"
            )

        # BAR 2 belongs to the workspace: the same buttons in the same order
        # on every one of its pages, whatever else changes.
        expected = None
        for page in workspace.pages:
            workspace.go_to(page.key)
            total_pages += 1

            assert workspace.page is page, f"{page.key}: page did not switch"
            check_actions(window, workspace, page)

            labels = list(window.context_bar.buttons)
            if expected is None:
                expected = labels
            assert labels == expected, (
                f"{workspace.key}: bar changed on {page.key}: "
                f"{labels} != {expected}"
            )

        # A page the panel does not link to must leave no row lit: the user is
        # somewhere none of the links point.
        unlinked = [p for p in workspace.pages
                    if p.key not in [k for k, _ in links]]
        if unlinked and side is not None:
            workspace.go_to(unlinked[0].key)
            assert side.currentRow() == -1, (
                f"{workspace.key}: {unlinked[0].key} lit a link row"
            )
        workspace.go_to(workspace.pages[0].key)

    check_persistence(window)
    check_diagnostics(window)

    print(f"OK: {len(workspaces)} workspaces / {total_pages} pages, "
          f"two bars swap, preferences round-trip, diagnostics render")
    app.quit()
    return 0


def check_actions(window, workspace, page) -> None:
    """BAR 2 shows exactly what the workspace declared, on every page."""
    specs = [s for s in workspace.build_actions() if isinstance(s, ActionSpec)]
    labels = list(window.context_bar.buttons)
    expected = [s.label for s in specs]
    assert labels == expected, f"{page.key}: context bar {labels} != {expected}"

    # A button is clickable exactly when its action has something to do.
    # An enabled button with no handler is a control that lies.
    by_label = {s.label: s for s in specs}
    for label, button in window.context_bar.buttons.items():
        spec = by_label[label]
        assert button.isEnabled() == spec.clickable, (
            f"{page.key}: {label} enabled={button.isEnabled()} "
            f"but clickable={spec.clickable}"
        )
        if button.isEnabled():
            assert spec.on_click is not None, (
                f"{page.key}: {label} is enabled with no handler"
            )

    # At most one primary, matching what the page declared.
    primaries = [l for l, b in window.context_bar.buttons.items()
                 if b.objectName() == "ContextPrimary"]
    declared = [s.label for s in workspace.build_actions()
                if isinstance(s, ActionSpec) and s.primary]
    assert primaries == declared, f"{page.key}: primary {primaries} != {declared}"
    assert len(primaries) <= 1, f"{page.key}: {len(primaries)} primaries"


def check_diagnostics(window) -> None:
    """The report must never be the thing that breaks when something breaks."""
    from .diagnostics import text

    report = text(window)
    for heading in ("# Desk Buddy Studio", "## Environment", "## Broker",
                    "## Paths", "## Window"):
        assert heading in report, f"diagnostics missing {heading!r}"
    assert "FAILED:" not in report, f"a diagnostics section raised:\n{report}"


def check_persistence(window) -> None:
    """Preferences a user changed must come back on the next launch.

    Runs against a throwaway settings file so a CI box — or the developer's own
    machine — never has its real preferences rewritten by the test.
    """
    path = tempfile.mktemp(suffix=".ini")
    window._settings = Settings(QSettings(path, QSettings.IniFormat))

    # The window restored the developer's real zoom on the way up, so what
    # zoom_in() lands on is relative to that — not to the default. Reset
    # first and the expected value is a constant again.
    window.zoom_reset()

    # Calibration, and its third step: workspace index and page key both have
    # to come back, so the workspace under test is one with several pages.
    window.select_workspace(3)
    window.workspace.go_to("visual")
    window.zoom_in()
    window.set_theme("dark")
    window.close()          # closeEvent is what saves

    saved = Settings(QSettings(path, QSettings.IniFormat))
    assert saved.get(keys.THEME) == "dark", saved.get(keys.THEME)
    assert saved.get(keys.LAST_WORKSPACE) == 3, saved.get(keys.LAST_WORKSPACE)
    assert saved.get(keys.LAST_PAGE) == "visual", saved.get(keys.LAST_PAGE)
    assert saved.get(keys.ZOOM_INDEX) == DEFAULT_ZOOM_INDEX + 1, (
        saved.get(keys.ZOOM_INDEX)
    )
    assert saved.get(keys.GEOMETRY), "window geometry not saved"

    os.unlink(path)


if __name__ == "__main__":
    raise SystemExit(main())
