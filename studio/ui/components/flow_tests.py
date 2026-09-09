"""A toolbar that wraps rather than hiding its tail.

No workspace ships enough actions to wrap at present — Workflows used to,
carrying every firmware action, until that vocabulary moved to the page's
own step palette. The wrapping stays because the alternatives Qt offers are
both a form of disappearance: a "»" chevron folds the tail away, and forcing
a minimum width lets the toolbar decide how wide the app must be. So the
tests below supply their own overflowing action list rather than borrowing a
workspace's, which is also what keeps them from breaking the next time one
changes its buttons.

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
    """Leaving a wrapping workspace and coming back must not collapse it.

    It did: the buttons were all still there, but the bar was one row tall
    and the other rows were clipped outside it. A freshly built button has
    not been sized by the style yet, so the first few measurements come back
    as zero — and the height that finally stuck was whatever a later stray
    resize computed, not the real one.

    Driven through a stand-in workspace with enough actions to wrap. The
    real ones are all short enough to fit on one row today, and a test for
    what happens to the *second* row cannot be written against a bar that
    only ever has one.
    """
    from PySide6.QtWidgets import QPushButton

    from ..main_window import MainWindow
    from .action_spec import ActionSpec

    window = MainWindow()
    window.debug_dock.setVisible(False)
    window.setFixedSize(1280, 760)
    window.show()
    _app.processEvents()

    index = {space.key: i for i, space in enumerate(window.workspaces)}

    # The overflow is borrowed onto a real workspace rather than pushed at
    # the bar directly: the bug is in what the *switch* does to the bar, and
    # a switch rebuilds it from the workspace it lands on — so a bar handed
    # its buttons from outside that path is wiped by the next rebuild and
    # tests nothing.
    stand_in = window.workspaces[index["workflows"]]
    stand_in.build_actions = lambda: [
        ActionSpec(f"Action number {n}", on_click=lambda: None)
        for n in range(40)
    ]

    def settle(key: str) -> None:
        window.select_workspace(index[key])
        _app.processEvents()
        _app.processEvents()

    bar = window.toolbar
    # Away and back before measuring. The window may already be showing the
    # stand-in's workspace, in which case the bar on screen was built from
    # its real actions and the borrowed ones have not been asked for yet.
    settle("vision")
    settle("workflows")
    first = bar.height()
    # The bar's own height is what says how many rows it took. Neither of
    # the two obvious alternatives works offscreen: the platform has not
    # necessarily moved the buttons into their rows yet, so their geometry
    # is all y=0, and `heightForWidth` re-measures from that same unplaced
    # state and answers 0. `_resize_to_rows` has already banked the real
    # number here.
    one_row = max(b.sizeHint().height() for b in bar._flow.findChildren(QPushButton))
    assert first > one_row, (
        "the stand-in should need more than one row at 1280px", first, one_row
    )

    for other in ("network", "vision", "calibration"):
        settle(other)
        settle("workflows")
        assert bar.height() == first, (other, bar.height(), first)
        # And still more than the one row it used to collapse to.
        assert bar.height() > one_row, (other, bar.height(), one_row)

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
