"""Input controls for the Calibration workspace.

Everything on these pages moves a physical arm. That one fact is what makes
the stock Qt widgets wrong here, and it is the same fact in two places:

`ScrollSafeSpinBox` and `PinnedSlider` both refuse the scroll wheel. Qt sends
the wheel to whatever sits under the pointer, so scrolling down a tall
calibration form drove every servo it passed on the way — silently, on values
the user was not looking at. A control that moves metal must not be reachable
by an input aimed somewhere else.

`PinnedSlider` also keeps the saved value visible. A slider nudged by accident
cannot be undone by looking at it: the arm has already gone there, and the
number it was at is gone from the screen. So the saved value is drawn on the
groove as a pin and kept as a number the caller can put back — two things at
once:

    the handle   where this joint has been *asked* to go
    the pin      the value that was saved

The pin belongs to whichever control owns it and to nothing else. On the IK
page each of the six hover points pins its own captured angle, so the marks
differ from row to row; pinning something shared — the live arm, say — makes
every row show the same mark and capturing one point look like it moved them
all. `snap_back()` returns the handle to the pin, which is the accident's undo.

These live here rather than in `ui/components/` because the reasoning is local
to calibration: a spin box in a settings dialog has every right to answer the
wheel, and `ui/components/manual_control.py` deliberately commits on it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QSlider,
    QStyle,
    QStyleOptionSlider,
)


def widest(widget, *texts: str) -> int:
    """How wide `widget` must be to show the longest of `texts` in full.

    Asked of the widget rather than measured from its font, so whatever the
    stylesheet adds around the text — padding, a border — is counted by the
    thing that applies it.

    Use this instead of `setFixedWidth`. Every hardcoded width on these pages
    was a guess at this number: correct at the zoom it was eyeballed on, and
    clipping above it. A button sized 60px for "Edit" rendered as "di" at the
    zoom the user actually ran, and a readout fixed at 72px showed "now 18"
    where the arm was at 180°.
    """
    remember = widget.text()
    try:
        needed = 0
        for text in texts:
            widget.setText(text)
            needed = max(needed, widget.sizeHint().width())
        return needed
    finally:
        widget.setText(remember)


class ScrollSafeSpinBox(QDoubleSpinBox):
    """A spin box the scroll wheel cannot change.

    Same reason as PinnedSlider.wheelEvent: on a tall form the wheel belongs
    to the page, and a value that quietly changes because the user scrolled
    past it is a value nobody chose. Click it and type, or use the arrows.
    """

    def __init__(self) -> None:
        super().__init__()
        # Without this the box takes focus on a click-through and then also
        # answers the wheel, which is the same bug by a longer route.
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event) -> None:
        event.ignore()


class PinnedSlider(QSlider):
    """A horizontal slider with a ghost pin at the arm's live value."""

    committed = Signal(int)

    def __init__(self, minimum: int, maximum: int, value: int) -> None:
        super().__init__(Qt.Horizontal)
        self.setRange(minimum, maximum)
        self.setValue(value)
        self._pin: int | None = None
        # Selects this page's slider skin, and with it the pin colour — the
        # QSS sets `color` on the widget for exactly that, since a stylesheet
        # cannot reach a custom paintEvent any other way.
        self.setProperty("calibrationPin", True)
        # Click or tab to reach it, never hover. With Qt's default a slider
        # can take focus from the pointer passing over it, which then routes
        # arrow keys to a joint the user never selected.
        self.setFocusPolicy(Qt.StrongFocus)
        # A drag is a servo command per release, not per pixel: tracking is
        # on so the readout follows the hand, but only the release commits.
        self.setTracking(True)
        self.sliderReleased.connect(lambda: self.committed.emit(self.value()))

    # ---- the pin ---------------------------------------------------------
    @property
    def pin(self) -> int | None:
        """The saved value, or None when nothing has been saved yet."""
        return self._pin

    def set_pin(self, value: int | None) -> None:
        if value == self._pin:
            return
        self._pin = value
        self.update()

    def snap_back(self) -> None:
        """Put the handle back on the saved value and commit it."""
        if self._pin is None or self.value() == self._pin:
            return
        self.setValue(self._pin)
        self.committed.emit(self._pin)

    @property
    def drifted(self) -> bool:
        """Whether the handle has been moved off the saved value."""
        return self._pin is not None and self.value() != self._pin

    # ---- keyboard commits immediately ------------------------------------
    # A drag has a release to commit on; a key press does not, so it commits
    # per step. Without this the control is unusable without a mouse.
    def keyPressEvent(self, event) -> None:
        before = self.value()
        super().keyPressEvent(event)
        if self.value() != before:
            self.committed.emit(self.value())

    def wheelEvent(self, event) -> None:
        """Ignored, always — the wheel scrolls the page instead.

        These sliders sit on a tall scrolling form, and Qt sends the wheel to
        whatever widget is under the pointer. Scrolling past a row therefore
        drove its servo and rewrote its angle, silently, on a point the user
        was not even looking at — several joints at once on the way down the
        page. A control that moves a physical arm must not be reachable by an
        input the user is aiming somewhere else.

        `event.ignore()` rather than swallowing it: the event carries on to
        the scroll area, so the page scrolls the way it would over any other
        widget.
        """
        event.ignore()

    # ---- drawing ---------------------------------------------------------
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._pin is None:
            return

        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(
            QStyle.CC_Slider, option, QStyle.SC_SliderGroove, self
        )
        handle = self.style().subControlRect(
            QStyle.CC_Slider, option, QStyle.SC_SliderHandle, self
        )

        # Positioned the way Qt positions the handle, so the pin lands under
        # it exactly when the values match rather than a pixel or two off.
        span = groove.width() - handle.width()
        offset = QStyle.sliderPositionFromValue(
            self.minimum(), self.maximum(), self._pin, span, option.upsideDown
        )
        x = groove.left() + offset + handle.width() / 2

        # Never the accent: that is the groove fill and the handle border,
        # and a pin in the same colour reads as part of the slider's own
        # value rather than as the separate fact it is. The QSS hands this
        # widget the theme's `warn` through `color`.
        colour = QColor(self.palette().windowText().color())
        colour.setAlphaF(1.0 if self.drifted else 0.45)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(colour, 2.0, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(x, groove.top() - 1, x, groove.bottom() + 1)
        painter.end()
