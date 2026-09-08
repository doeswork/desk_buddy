"""A layout that wraps its children onto as many rows as they need.

Qt ships nothing like this. QHBoxLayout runs off the edge, and a QToolBar
folds its tail behind a "»" chevron — both of which hide controls rather than
showing them, which is the one thing a palette of actions must not do.

The implementation is the standard Qt flow-layout pattern: `heightForWidth`
answers "given this width, how tall do I need to be", and `setGeometry` lays
the items out for real. Both walk the same loop, which is why `_lay_out` takes
a `place` flag rather than existing twice.
"""

from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidget


class FlowLayout(QLayout):
    """Left-to-right, wrapping onto a new row when the width runs out."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        margin: int = 0,
        spacing: int = 4,
        line_spacing: int = 4,
    ) -> None:
        super().__init__(parent)
        self._items: list = []
        self._line_spacing = line_spacing
        self.setContentsMargins(QMargins(margin, margin, margin, margin))
        self.setSpacing(spacing)

    # ---- QLayout plumbing ------------------------------------------------
    def addItem(self, item) -> None:          # noqa: N802 - Qt's name
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):             # noqa: N802 - Qt's name
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):             # noqa: N802 - Qt's name
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:   # noqa: N802
        # Nothing to expand into: the layout is exactly as tall as its rows.
        return Qt.Orientations(Qt.Orientation(0))

    # ---- the wrapping itself ---------------------------------------------
    def hasHeightForWidth(self) -> bool:      # noqa: N802 - Qt's name
        return True

    def heightForWidth(self, width: int) -> int:      # noqa: N802 - Qt's name
        return self._lay_out(QRect(0, 0, width, 0), place=False)

    def setGeometry(self, rect: QRect) -> None:       # noqa: N802 - Qt's name
        super().setGeometry(rect)
        self._lay_out(rect, place=True)

    def sizeHint(self) -> QSize:              # noqa: N802 - Qt's name
        return self.minimumSize()

    def minimumSize(self) -> QSize:           # noqa: N802 - Qt's name
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(
            margins.left() + margins.right(),
            margins.top() + margins.bottom(),
        )

    def _lay_out(self, rect: QRect, *, place: bool) -> int:
        """Walk the items in rows. Returns the total height needed.

        Called twice per resize — once to measure, once to place — so it must
        not mutate anything when `place` is False.
        """
        margins = self.contentsMargins()
        area = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x, y = area.x(), area.y()
        row_height = 0

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self.spacing()
            if row_height and next_x - self.spacing() > area.right():
                # Does not fit on this row: drop to the next one.
                x = area.x()
                y += row_height + self._line_spacing
                next_x = x + hint.width() + self.spacing()
                row_height = 0

            if place:
                item.setGeometry(QRect(QPoint(x, y), hint))

            x = next_x
            row_height = max(row_height, hint.height())

        return y + row_height - rect.y() + margins.bottom()


class FlowBar(QWidget):
    """A widget whose children flow onto as many rows as they need.

    Wraps FlowLayout with the one piece of glue a wrapping layout always
    needs: a height that follows from the width it is given. Without it the
    parent lays the bar out at one row's height and the wrapped rows are
    simply clipped.
    """

    def __init__(self, parent: QWidget | None = None, **kwargs) -> None:
        super().__init__(parent)
        self._flow = FlowLayout(self, **kwargs)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

    def add(self, widget: QWidget) -> None:
        # Parented before it is polished, and polished before it is measured.
        # A widget with no parent has no stylesheet to be polished against,
        # so it reports a 0x0 size hint — and a layout measuring a row of
        # those concludes it needs a single line. That is what left a bar
        # rebuilt and measured in one breath (exactly what a workspace switch
        # does) one row tall, with the rest of its buttons outside the strip.
        widget.setParent(self)
        widget.ensurePolished()
        widget.adjustSize()
        self._flow.addWidget(widget)

    def count(self) -> int:
        return self._flow.count()

    def relayout(self) -> None:
        """Place the children for the width this widget currently has.

        Qt normally does this off a resize event, which is only delivered to
        a widget that has been shown. A bar built and measured before its
        window appears — and every test — would otherwise leave every child
        stacked at the origin.
        """
        self._flow.setGeometry(self.rect())
        self.setMinimumHeight(self._flow.heightForWidth(self.width()))

    def clear(self) -> None:
        while self._flow.count():
            item = self._flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def hasHeightForWidth(self) -> bool:      # noqa: N802 - Qt's name
        return True

    def heightForWidth(self, width: int) -> int:      # noqa: N802 - Qt's name
        return self._flow.heightForWidth(width)

    def resizeEvent(self, event) -> None:     # noqa: N802 - Qt's name
        super().resizeEvent(event)
        # A width change can change the row count, and therefore the height
        # this widget needs. Qt does not re-ask on its own for a layout whose
        # height depends on width, so it is asked here.
        self.setMinimumHeight(self._flow.heightForWidth(self.width()))
