"""A toolbar that wraps rather than hiding its tail.

The toolbar carries every firmware action in the Workflows workspace — more buttons
than fit on one row of any usable window. The two alternatives Qt offers are
both a form of disappearance: a "»" chevron folds the tail away, and forcing
a minimum width lets the toolbar decide how wide the app must be.

    QT_QPA_PLATFORM=offscreen python -m studio.ui.components.flow_tests
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from .flow_layout import FlowBar

_app = QApplication.instance() or QApplication([])


def bar(count: int = 28, *, button_width: int = 110) -> FlowBar:
    holder = FlowBar(spacing=4, line_spacing=4)
    for index in range(count):
        button = QPushButton(f"Button {index}")
        button.setFixedSize(button_width, 26)
        holder.add(button)
    return holder


def test_a_narrower_bar_needs_more_rows() -> None:
    """The whole point: nothing is hidden, so the height gives instead."""
    holder = bar()
    heights = [holder.heightForWidth(width) for width in (1600, 1200, 800, 400)]
    assert heights == sorted(heights), heights
    assert heights[0] < heights[-1]


def test_every_button_is_placed_not_clipped() -> None:
    """A wrapped row is only useful if its buttons are actually on screen."""
    holder = bar()
    width = 640
    holder.resize(width, holder.heightForWidth(width))
    holder.relayout()

    buttons = holder.findChildren(QPushButton)
    assert len(buttons) == 28
    for button in buttons:
        assert button.geometry().right() <= width, button.text()
        assert button.geometry().bottom() <= holder.height(), button.text()


def test_rows_do_not_overlap() -> None:
    """Two buttons sharing a row must not share a column, and a row below
    must start beneath the one above — the failure mode of a flow layout is
    quietly stacking items on top of each other."""
    holder = bar()
    holder.resize(500, holder.heightForWidth(500))
    holder.relayout()

    boxes = [b.geometry() for b in holder.findChildren(QPushButton)]
    for first in range(len(boxes)):
        for second in range(first + 1, len(boxes)):
            assert not boxes[first].intersects(boxes[second]), (first, second)


def test_an_empty_bar_takes_no_height() -> None:
    """A workspace with no actions gets no strip, rather than an empty one."""
    holder = FlowBar()
    assert holder.count() == 0
    assert holder.heightForWidth(800) <= 0


def test_clearing_removes_every_button() -> None:
    """The bar is rebuilt on every workspace switch, so a stale button left
    behind would be a control from somewhere else entirely."""
    holder = bar(count=6)
    holder.clear()
    _app.processEvents()
    assert holder.count() == 0


# ---- The bar in a real window --------------------------------------------


def test_the_bar_keeps_its_rows_across_workspace_switches() -> None:
    """Leaving Workflows and coming back must not collapse the palette.

    It did: the buttons were all still there, but the bar was one row tall
    and the other rows were clipped outside it. A freshly built button has
    not been sized by the style yet, so the first few measurements come back
    as zero — and the height that finally stuck was whatever a later stray
    resize computed, not the real one.
    """
    from PySide6.QtWidgets import QPushButton

    from ..main_window import MainWindow

    window = MainWindow()
    window.debug_dock.setVisible(False)
    window.setFixedSize(1280, 760)
    window.show()
    _app.processEvents()

    index = {space.key: i for i, space in enumerate(window.workspaces)}

    def settle(key: str) -> None:
        window.select_workspace(index[key])
        _app.processEvents()
        _app.processEvents()

    bar = window.toolbar
    settle("workflows")
    first = bar.height()
    rows = len({b.geometry().y() for b in bar._flow.findChildren(QPushButton)})
    assert rows > 1, "the palette should need more than one row at 1280px"

    for other in ("network", "vision", "calibration", "manual"):
        settle(other)
        settle("workflows")
        assert bar.height() == first, (other, bar.height(), first)
        # And every row is inside the bar, not clipped below it.
        assert bar._flow.heightForWidth(window.width()) <= bar.height()

    window.close()


def test_the_toolbar_sits_below_the_workspace_bar() -> None:
    """Two rows, never one.

    `restoreState` matches toolbars by object name, so a window state saved
    before either bar was renamed restores neither — and the break between
    them goes too, dropping the toolbar up onto the workspace bar's row.
    The break is re-asserted after every restore for exactly that reason.
    """
    from ..main_window import MainWindow

    window = MainWindow()
    window.show()
    _app.processEvents()

    assert window.toolbar.y() > window.workspace_bar.y(), (
        window.workspace_bar.y(), window.toolbar.y()
    )
    assert window.toolBarBreak(window.toolbar)
    window.close()


def test_stacking_survives_a_saved_window_state() -> None:
    """The state a running app writes must restore to the same layout."""
    from ..main_window import MainWindow

    first = MainWindow()
    first.show()
    _app.processEvents()
    state = first.saveState()
    first.close()

    second = MainWindow()
    second.restoreState(state)
    second.insertToolBarBreak(second.toolbar)
    second.show()
    _app.processEvents()

    assert second.toolbar.y() > second.workspace_bar.y()
    second.close()


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} flow layout tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
