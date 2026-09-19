"""Scrolling in the tray's logs.

Following the newest line was always the intent, but it used to be inferred
from where the scrollbar happened to be: `_replace` re-pinned to the bottom
only when the view was already within a couple of pixels of it. On a busy
broker that is lost by accident — one nudge of the wheel to read a line that
just scrolled past, and the log quietly stops following while traffic piles
up below with nothing on screen saying so.

The MQTT tab has a "Stick to bottom" checkbox now, so following is a setting
rather than a side effect of scroll position. These cover the three states
that produces, plus the one that bit the old code: a log that shrinks.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPlainTextEdit

from .debug_tray import DebugTray

_app = QApplication.instance() or QApplication([])


def _log() -> QPlainTextEdit:
    """A read-only view short enough that any real content overflows it."""
    output = QPlainTextEdit()
    output.setReadOnly(True)
    output.resize(400, 100)
    output.show()
    return output


def _lines(count: int) -> str:
    return "\n".join(f"message {index}" for index in range(count))


def test_sticking_to_the_bottom_follows_from_anywhere() -> None:
    """The point of the checkbox: the tail comes to the user.

    Being scrolled to the very top is the case the old behaviour got wrong,
    because it read that position as "not following" and left it there.
    """
    output = _log()
    DebugTray._replace(output, _lines(200), follow=True)
    scroll = output.verticalScrollBar()
    scroll.setValue(0)

    DebugTray._replace(output, _lines(400), follow=True)
    assert scroll.value() == scroll.maximum(), (scroll.value(), scroll.maximum())


def test_unsticking_holds_the_line_the_user_is_reading() -> None:
    """Untick, scroll back, and new traffic must not drag the view forward.

    `setPlainText` resets the scrollbar's range, so the position has to be
    restored explicitly: the same numeric value against a larger maximum is
    a different place, and on a log that only grows it creeps toward the end.
    """
    output = _log()
    DebugTray._replace(output, _lines(400), follow=False)
    scroll = output.verticalScrollBar()
    scroll.setValue(120)

    DebugTray._replace(output, _lines(800), follow=False)
    assert scroll.value() == 120, scroll.value()


def test_the_tabs_without_a_checkbox_keep_the_old_behaviour() -> None:
    """Errors and Page share `_replace` and have no control of their own, so
    omitting `follow` must still tail only when already parked at the end."""
    output = _log()
    DebugTray._replace(output, _lines(400))
    scroll = output.verticalScrollBar()

    scroll.setValue(scroll.maximum())
    DebugTray._replace(output, _lines(800))
    assert scroll.value() == scroll.maximum()

    scroll.setValue(50)
    DebugTray._replace(output, _lines(1200))
    assert scroll.value() == 50, scroll.value()


def test_a_cleared_log_does_not_keep_a_position_that_no_longer_exists() -> None:
    """Clear replaces thousands of lines with a handful. A held position from
    the old text is out of range in the new one, and a scrollbar set past its
    maximum is a view scrolled somewhere there is nothing to see."""
    output = _log()
    DebugTray._replace(output, _lines(1200), follow=False)
    scroll = output.verticalScrollBar()
    scroll.setValue(scroll.maximum())

    DebugTray._replace(output, _lines(5), follow=False)
    assert scroll.value() <= scroll.maximum(), (scroll.value(), scroll.maximum())


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} debug tray scroll tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
