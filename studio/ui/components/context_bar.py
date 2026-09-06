"""BAR 2 — the context bar.

Holds the actions for the workspace you are in, and is rebuilt from scratch on
every switch. That swap is the core of the FreeCAD model: pick a workspace and
the whole toolset below it changes.

Within one workspace it does not change shape. The same controls stay in the
same places on every page; what moves is which of them are live. A workspace
declares them (see workspaces/base.py) as ActionSpecs — one may be marked
primary, which renders filled; the rest render outlined. An action with no
handler renders disabled rather than being left out.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QPushButton, QToolBar

from .action_spec import Separator


class ContextButton(QPushButton):
    """One action on the context bar.

    `primary=True` gives the filled treatment — at most one per page, or none.
    Styling lives in theme/qss.py under QPushButton#ContextPrimary / #ContextAction.
    """

    def __init__(self, text: str, *, primary: bool = False, enabled: bool = False) -> None:
        super().__init__(text)
        self.setObjectName("ContextPrimary" if primary else "ContextAction")
        self.setCursor(Qt.PointingHandCursor)
        self.setEnabled(enabled)
        self.setFlat(True)


class ContextBar(QToolBar):
    def __init__(self, window: QMainWindow) -> None:
        super().__init__("Actions", window)
        self.setObjectName("ContextBar")
        self.setMovable(False)
        # QSS margins cannot pull back the spacing the toolbar's own layout
        # inserts, so the strip has to be closed here.
        self.layout().setSpacing(0)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._window = window
        self.buttons: dict[str, ContextButton] = {}

    def show_page(self, source) -> None:
        """Rebuild the bar from whatever declares actions — a workspace."""
        self.clear()
        self.buttons = {}

        for spec in source.build_actions():
            if isinstance(spec, Separator):
                self.addSeparator()
                continue

            button = ContextButton(
                spec.label,
                primary=spec.primary,
                enabled=spec.clickable,
            )
            if spec.on_click is not None:
                button.clicked.connect(spec.on_click)
            self.addWidget(button)
            self.buttons[spec.label] = button

        # No overflow chevron. The point of a bar that keeps its shape is that
        # every control stays where the user last saw it, and folding the tail
        # of it behind a "»" is the same disappearance by another route. Qt
        # only offers the extension button when the bar is too narrow, so the
        # fix is to refuse to be: claim the width the buttons actually need.
        self.setMinimumWidth(self.sizeHint().width())
