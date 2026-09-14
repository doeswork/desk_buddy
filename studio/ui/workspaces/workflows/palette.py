"""The step palette: one button per group, level with the workflow's name.

Where this sits, and why it is not the toolbar. The toolbar holds what a
workspace does to the *document* — make one, save it, delete it — and it
keeps that shape on every page. The step vocabulary is not that: it is the
content of the one document on screen, it is long, and it grew until the bar
wrapped to several rows of thirty near-identical buttons. A bar that big
stops being a toolbar and becomes a wall the page has to be found under.

So the palette lives in the page header, on the same line as the workflow it
appends to. That line is where "this document, and what you can put in it"
belongs, and it puts the vocabulary beside the JSON it edits rather than a
window's height away from it.

And it is grouped. `steps.GROUPS` was always the real shape of the
vocabulary; the toolbar flattened it and wrote every leaf out by name, which
is how three buttons came to be called Elbow, Wrist and Twist — three names
for one verb, competing for the eye with Photo and Firmware update. One
button per group, each opening its own menu, is nine things to scan instead
of thirty, and it says what the robot is made of on the way past.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMenu,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from .steps import GROUPS, StepGroup


class GroupButton(QToolButton):
    """One group of steps, opening as a menu.

    A QToolButton rather than a QPushButton with a menu attached: it draws
    the drop indicator itself, and `InstantPopup` makes the whole button the
    menu's trigger, so there is no half of it that looks clickable and does
    nothing.
    """

    def __init__(
        self, group: StepGroup, on_pick: Callable[[str], object] | None
    ) -> None:
        super().__init__()
        self.setObjectName("PaletteGroup")
        self.setText(group.label)
        self.setPopupMode(QToolButton.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.setCursor(Qt.PointingHandCursor)
        self.setEnabled(on_pick is not None)
        self.group_label = group.label

        menu = QMenu(self)
        menu.setObjectName("PaletteMenu")
        # Menus keep tooltips off unless asked; without this the summary
        # written below never reaches the screen.
        menu.setToolTipsVisible(True)
        for template in group.templates:
            action = menu.addAction(template.label)
            # The note is the part nobody can guess — the units, the legal
            # range, the calibration a field depends on — so it is shown with
            # the summary rather than kept for the source file.
            action.setToolTip(f"{template.summary} {template.notes}".strip())
            action.setData(template.key)
            if on_pick is not None:
                action.triggered.connect(
                    lambda _checked=False, key=template.key: on_pick(key)
                )
        self.setMenu(menu)


class StepPalette(QWidget):
    """The whole vocabulary, as a row of group buttons.

    Dead as a whole when no workflow is open, for the same reason Save is:
    there is nothing to append a step to. Disabled rather than hidden — a
    palette that vanishes takes with it the answer to "what can this thing
    even do", which is most of what a first look at the page is for.
    """

    def __init__(self, on_pick: Callable[[str], object] | None) -> None:
        super().__init__()
        self.setObjectName("StepPalette")
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.buttons: dict[str, GroupButton] = {}
        for group in GROUPS:
            button = GroupButton(group, on_pick)
            self.buttons[group.label] = button
            layout.addWidget(button)


__all__ = ["GroupButton", "StepPalette"]
