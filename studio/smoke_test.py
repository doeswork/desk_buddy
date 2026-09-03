"""Headless check that the UI builds. Runs in CI before the slow bundle step.

    QT_QPA_PLATFORM=offscreen python -m studio.smoke_test
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from .storage.user_config import keys
from .storage.user_config.settings import Settings
from .ui.components import ActionSpec
from .ui.main_window import MainWindow


def main() -> int:
    app = QApplication([])
    window = MainWindow()
    window.show()

    assert window.windowTitle() == "Desk Buddy Studio"

    pages = window.pages_list
    assert len(pages) == 6, f"expected 6 pages, got {len(pages)}"
    assert window.pages.count() == len(pages), "page missing"

    for index, page in enumerate(pages):
        window.select_page(index)

        assert window.pages.currentIndex() == index, f"{page.key}: page did not switch"
        assert window.nav_bar.actions_by_index[index].isChecked(), f"{page.key}: not checked"

        # BAR 2 holds exactly this page's actions.
        specs = [s for s in page.build_actions() if isinstance(s, ActionSpec)]
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
        declared = [s.label for s in page.build_actions()
                    if isinstance(s, ActionSpec) and s.primary]
        assert primaries == declared, f"{page.key}: primary {primaries} != {declared}"
        assert len(primaries) <= 1, f"{page.key}: {len(primaries)} primaries"

        # The page builds its own side panel.
        side = page.side()
        assert side is not None, f"{page.key}: no side panel"


    check_persistence(window)
    check_diagnostics(window)

    print(f"OK: {len(pages)} pages, two bars swap, "
          f"preferences round-trip, diagnostics render")
    app.quit()
    return 0


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

    window.select_page(3)
    window.zoom_in()
    window.set_theme("dark")
    window.close()          # closeEvent is what saves

    saved = Settings(QSettings(path, QSettings.IniFormat))
    assert saved.get(keys.THEME) == "dark", saved.get(keys.THEME)
    assert saved.get(keys.LAST_PAGE) == 3, saved.get(keys.LAST_PAGE)
    assert saved.get(keys.ZOOM_INDEX) == 3, saved.get(keys.ZOOM_INDEX)
    assert saved.get(keys.GEOMETRY), "window geometry not saved"

    os.unlink(path)


if __name__ == "__main__":
    raise SystemExit(main())
