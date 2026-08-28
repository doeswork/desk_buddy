"""Headless check that the UI builds. Runs in CI before the slow bundle step.

    QT_QPA_PLATFORM=offscreen python -m studio.smoke_test
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

try:
    from .ui.components import ActionSpec
    from .ui.main_window import MainWindow
except ImportError:
    from ui.components import ActionSpec
    from ui.main_window import MainWindow


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
        labels = list(window.context_bar.buttons)
        expected = [s.label for s in page.build_actions() if isinstance(s, ActionSpec)]
        assert labels == expected, f"{page.key}: context bar {labels} != {expected}"
        for label, button in window.context_bar.buttons.items():
            assert not button.isEnabled(), f"{page.key}: {label} enabled"

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



    print(f"OK: {len(pages)} pages, two bars swap, all actions disabled")
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
