"""The toolbar: the actions for the workspace you are in.

The second of the app's three bars. The menu bar is Qt's own, the workspace
bar above this one picks which workspace you are in, and this holds whatever
that workspace can do. Subclasses QToolBar, so "toolbar" in this codebase
means this widget specifically and `QToolBar` means Qt's.

Rebuilt from scratch on every switch. That swap is the core of the FreeCAD
model: pick a workspace and the whole toolset below it changes.

Within one workspace it does not change shape. The same controls stay in the
same places on every page; what moves is which of them are live. A workspace
declares them (see workspaces/base.py) as ActionSpecs — one may be marked
primary, which renders filled; the rest render outlined. An action with no
handler renders disabled rather than being left out.

The bar wraps. A workspace with a real vocabulary to offer — Workflows hands
over every firmware action as an insertable step — has more buttons than fit
on one row of any window worth using, and the alternatives are both worse: a
"»" chevron hides the tail, and forcing the window wider makes the bar decide
how big the app is. Wrapping is the only one of the three that keeps every
control visible and clickable at any window size.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QToolBar,
)

from .action_spec import Separator
from .flow_layout import FlowBar

# Qt's own "no maximum" sentinel. PySide6 does not re-export QWIDGETSIZE_MAX,
# so the value is named here rather than left as a bare literal.
UNLIMITED_HEIGHT = (1 << 24) - 1


class ToolbarButton(QPushButton):
    """One action on the context bar.

    `primary=True` gives the filled treatment — at most one per page, or none.
    Styling lives in theme/qss.py under QPushButton#ToolbarPrimary / #ToolbarAction.
    """

    def __init__(self, text: str, *, primary: bool = False, enabled: bool = False) -> None:
        super().__init__(text)
        self.setObjectName("ToolbarPrimary" if primary else "ToolbarAction")
        self.setCursor(Qt.PointingHandCursor)
        self.setEnabled(enabled)
        self.setFlat(True)


class Toolbar(QToolBar):
    def __init__(self, window: QMainWindow) -> None:
        super().__init__("Actions", window)
        self.setObjectName("Toolbar")
        self.setMovable(False)
        # QSS margins cannot pull back the spacing the toolbar's own layout
        # inserts, so the strip has to be closed here.
        self.layout().setSpacing(0)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._window = window
        self.buttons: dict[str, ToolbarButton] = {}

        # One wrapping widget holds every action, rather than adding them to
        # The toolbar directly: QToolBar lays its own children out in a single
        # row and offers a chevron when they overflow, which is exactly the
        # disappearance this bar exists to avoid.
        self._flow = FlowBar(spacing=0, line_spacing=2)
        self._flow.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.addWidget(self._flow)

    def show_page(self, source) -> None:
        """Rebuild the bar from whatever declares actions — a workspace."""
        self._flow.clear()
        self.buttons = {}

        for spec in source.build_actions():
            if isinstance(spec, Separator):
                self._flow.add(self._separator())
                continue

            button = ToolbarButton(
                spec.label,
                primary=spec.primary,
                enabled=spec.clickable,
            )
            if spec.on_click is not None:
                button.clicked.connect(spec.on_click)
            self._flow.add(button)
            self.buttons[spec.label] = button

        # The bar is as tall as the rows it ended up with. Asked for at the
        # window's current width, because that is what decides the row count.
        self._resize_to_rows()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        self._resize_to_rows()

    def _resize_to_rows(self) -> None:
        """Match the bar's height to however many rows the buttons need.

        Re-run on every resize, so the row count follows the window rather
        than whatever it happened to be when the workspace was opened.
        """
        # Release last time's clamp before measuring. A workspace with no
        # actions pins the bar to zero height, and a pinned widget reports
        # nothing to lay out — so without this the bar came back from an
        # empty workspace with all its buttons and no height to show them
        # in, and stayed that way for the rest of the session.
        self.setMaximumHeight(UNLIMITED_HEIGHT)

        # A workspace with no actions gets no bar at all, rather than an
        # empty strip left at whatever height the last one needed.
        if not self._flow.count():
            self._flow.setMinimumHeight(0)
            self.setMinimumHeight(0)
            self.setMaximumHeight(0)
            return

        # Measured against the window, never `self.width()`. During a
        # workspace switch Qt resizes this bar several times on the way to
        # its final geometry, and some of those intermediate widths are
        # narrow enough to compute the wrong number of rows.
        margins = self.contentsMargins()
        width = self._window.width() - margins.left() - margins.right()
        height = self._flow.heightForWidth(width)

        # Buttons created moments ago have not been sized by the style yet,
        # so they measure 0x0 and the whole bar measures as nothing. Ask
        # again once Qt has caught up rather than accepting that: silently
        # skipping left the bar at whatever height a *later* stray resize
        # happened to compute — one row, with the other three clipped, every
        # time the user came back to Workflows from another workspace.
        if height <= 0:
            QTimer.singleShot(0, self._resize_to_rows)
            return

        self._flow.setMinimumHeight(height)
        self.setFixedHeight(height + 2)

    @staticmethod
    def _separator() -> QFrame:
        """A divider that survives wrapping.

        QToolBar.addSeparator() belongs to the toolbar's own row layout, so
        it cannot be used once the buttons live in a flow. This is the same
        hairline as a widget the flow can place like any other.
        """
        line = QFrame()
        line.setObjectName("ToolbarSeparator")
        line.setFrameShape(QFrame.VLine)
        line.setFixedWidth(9)
        return line
