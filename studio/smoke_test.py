"""Headless check that the UI builds. Runs in CI before the slow bundle step.

    QT_QPA_PLATFORM=offscreen python -m studio.smoke_test
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

try:
    from .ui.main_window import MainWindow
    from .ui.pages import SEPARATOR
except ImportError:
    from ui.main_window import MainWindow
    from ui.pages import SEPARATOR


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

        # BAR 2 holds exactly this page's actions, all disabled.
        labels = [a.text() for a in window.context_bar.actions() if a.text()]
        expected = [a for a in page.actions if a != SEPARATOR]
        assert labels == expected, f"{page.key}: context bar {labels} != {expected}"
        for action in window.context_bar.actions():
            if action.text():
                assert not action.isEnabled(), f"{page.key}: {action.text()} enabled"

        side = window.side_dock.widget()
        assert side.count() == len(page.side_items), f"{page.key}: side list"

    print(f"OK: {len(pages)} pages, two bars swap, all actions disabled")
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
