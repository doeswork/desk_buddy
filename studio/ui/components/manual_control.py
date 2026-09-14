"""Manual Controller — direct arm, base, gripper, and camera control.

A debug tray tab rather than a workspace. Driving a joint by hand is not a
place you go to get work done; it is what you do *while* doing something
else — checking a servo answers, nudging the arm off a limit, seeing whether
the broker is carrying anything at all. That is the tray's job description,
and it is why this sits beside the MQTT log rather than behind a workspace
switch that hides whatever you were looking at.

What commands actually go out is not here — that is
`services/manual_controller/live_actions.py`, which needs no window. This
file is the sliders and buttons that call it.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...services.manual_controller import LiveActions
from ..theme.metrics import CARD_GAP, CARD_SPACING, ROW_PADDING
from .card import Card
from .column import Column
from .flow_layout import FlowBar

# What one card may shrink to and grow to.
#
# The floor is a readable measure rather than the smallest the widgets can
# be squeezed to. Every card here wraps internally, so they *can* be driven
# down to about 150px — but a card that narrow turns its one-sentence
# description into six ragged lines and buys nothing, since wrapping onto
# the next row is the better way to use a narrow tray. Below this width the
# cards wrap instead of shrinking further; the tray scrolls if that makes
# them taller than the dock. The ceiling stops one card from claiming a
# whole wide tray when the others could sit beside it.
CARD_MIN_WIDTH = 300
CARD_MAX_WIDTH = 420


class CommitSlider(QSlider):
    """A slider that separates previewing a value from sending it.

    Mouse drags update the number continuously but commit once on release,
    matching the Rails range inputs' ``change`` behavior. Keyboard and wheel
    steps commit immediately so the control remains fully accessible.
    """

    committed = Signal(int)

    def __init__(self, minimum: int, maximum: int, value: int) -> None:
        super().__init__(Qt.Horizontal)
        self.setRange(minimum, maximum)
        self.setValue(value)
        self.setProperty("manualControl", True)
        self.setTracking(True)
        self.sliderReleased.connect(lambda: self.committed.emit(self.value()))

    def keyPressEvent(self, event) -> None:
        before = self.value()
        super().keyPressEvent(event)
        if self.value() != before:
            self.committed.emit(self.value())

    def wheelEvent(self, event) -> None:
        before = self.value()
        super().wheelEvent(event)
        if self.value() != before:
            self.committed.emit(self.value())


class SliderControl(QWidget):
    """A labelled range input with a synchronized numeric editor."""

    def __init__(
        self,
        label: str,
        minimum: int,
        maximum: int,
        value: int,
        suffix: str,
        *,
        enabled: bool,
        on_commit: Callable[[int], None],
    ) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(CARD_SPACING)

        caption = QLabel(label)
        caption.setObjectName("ManualControlLabel")
        layout.addWidget(caption)

        row = QHBoxLayout()
        row.setSpacing(ROW_PADDING)

        self.slider = CommitSlider(minimum, maximum, value)
        self.slider.setAccessibleName(label)
        self.slider.setEnabled(enabled)
        row.addWidget(self.slider, 1)

        self.value = QSpinBox()
        self.value.setRange(minimum, maximum)
        self.value.setValue(value)
        self.value.setSuffix(suffix)
        self.value.setKeyboardTracking(False)
        self.value.setAccessibleName(f"{label} value")
        self.value.setEnabled(enabled)
        self.value.setMinimumWidth(76)
        row.addWidget(self.value)
        layout.addLayout(row)

        self.slider.valueChanged.connect(self.value.setValue)
        self.value.valueChanged.connect(self.slider.setValue)
        self.slider.committed.connect(on_commit)
        self.value.editingFinished.connect(lambda: on_commit(self.value.value()))


class TargetCard(Card):
    def __init__(self, live: LiveActions) -> None:
        super().__init__("Target robot")
        self.setToolTip(
            "Commands publish on the selected robot's /test topic through "
            "Studio's configured MQTT account."
        )

        available = live.robots()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(ROW_PADDING)

        label = QLabel("Robot")
        label.setObjectName("ManualControlLabel")
        row.addWidget(label)

        self.combo = QComboBox()
        self.combo.setAccessibleName("Target robot")
        if available:
            names = [robot.name for robot in available]
            self.combo.addItems([robot.display_name for robot in available])
            if live.robot in names:
                self.combo.setCurrentIndex(names.index(live.robot))
            self.combo.currentIndexChanged.connect(
                lambda index: live.select_robot(names[index])
            )
        else:
            self.combo.addItem("No robots marked")
            self.combo.setEnabled(False)
        row.addWidget(self.combo, 1)

        topic = QLabel(
            f"{live.robot}/test" if live.robot else "Network → Robots"
        )
        topic.setObjectName("ManualTopic")
        row.addWidget(topic)
        self.layout().addLayout(row)


class JointCard(Card):
    JOINTS = (
        ("Elbow", "ELBOW", 90),
        ("Wrist", "WRIST", 90),
        ("Twist", "TWIST", 90),
    )

    def __init__(self, live: LiveActions, *, enabled: bool) -> None:
        super().__init__("Arm joints")
        self.setToolTip("Drag to preview; release to send the angle to the robot.")
        self.controls: dict[str, SliderControl] = {}
        for label, joint, default in self.JOINTS:
            control = SliderControl(
                label,
                0,
                180,
                default,
                "°",
                enabled=enabled,
                on_commit=lambda value, name=joint: live.servo(name, value),
            )
            self.controls[joint] = control
            self.layout().addWidget(control)


class ReachCard(Card):
    def __init__(self, live: LiveActions, *, enabled: bool) -> None:
        super().__init__("Reach")
        self.setToolTip("Move with the robot's calibrated inverse kinematics.")
        self.control = SliderControl(
            "Distance",
            1,
            120,
            60,
            " mm",
            enabled=enabled,
            on_commit=live.reach,
        )
        self.layout().addWidget(self.control)


def _labelled(text: str, control: QWidget) -> QWidget:
    """A label and its control as one thing, so a flow cannot split them."""
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(ROW_PADDING)
    row.addWidget(QLabel(text))
    row.addWidget(control)
    return holder


def control_button(
    label: str,
    on_click: Callable[[], None],
    *,
    enabled: bool,
    primary: bool = False,
) -> QPushButton:
    button = QPushButton(label)
    button.setObjectName("ToolbarPrimary" if primary else "ToolbarAction")
    button.setCursor(Qt.PointingHandCursor)
    button.setEnabled(enabled)
    button.clicked.connect(on_click)
    return button


class BaseCard(Card):
    SPEEDS = (
        ("Very slow", "veryslow"),
        ("Slow", "slow"),
        ("Regular", "regular"),
        ("Fast", "fast"),
        ("Super fast", "superfast"),
    )

    def __init__(self, live: LiveActions, *, enabled: bool) -> None:
        super().__init__("Base rotation")
        self.setToolTip("Move in encoder steps. 216 steps is one full turn.")

        # Steps and Speed wrap as two labelled units rather than four loose
        # widgets: a flow that may break anywhere puts "Speed" at the end of
        # one row and its dropdown at the start of the next, which reads as
        # a label for the wrong control.
        self.steps = QSpinBox()
        self.steps.setAccessibleName("Base steps")
        self.steps.setRange(1, 216)
        self.steps.setValue(1)
        self.steps.setEnabled(enabled)

        self.speed = QComboBox()
        self.speed.setAccessibleName("Base speed")
        for label, value in self.SPEEDS:
            self.speed.addItem(label, value)
        self.speed.setCurrentIndex(1)
        self.speed.setEnabled(enabled)

        settings = FlowBar(spacing=ROW_PADDING, line_spacing=ROW_PADDING)
        settings.add(_labelled("Steps", self.steps))
        settings.add(_labelled("Speed", self.speed))
        self.layout().addWidget(settings)

        # The buttons wrap inside the card. A fixed row is what made this
        # card 294px wide no matter how narrow the tray got, which is a card
        # that hangs off the edge rather than one that gets smaller.
        buttons = FlowBar(spacing=0, line_spacing=ROW_PADDING)
        buttons.add(control_button(
            "← Left",
            lambda: live.rotate(
                "LEFT", self.steps.value(), str(self.speed.currentData())
            ),
            enabled=enabled,
        ))
        buttons.add(control_button(
            "Right →",
            lambda: live.rotate(
                "RIGHT", self.steps.value(), str(self.speed.currentData())
            ),
            enabled=enabled,
        ))
        buttons.add(control_button(
            "Home base",
            live.home,
            enabled=enabled,
        ))
        self.layout().addSpacing(CARD_GAP)
        self.layout().addWidget(buttons)


class QuickActionsCard(Card):
    def __init__(self, live: LiveActions, *, enabled: bool) -> None:
        super().__init__("Gripper & camera")
        self.setToolTip("The same quick actions from the original live controller.")
        # Five buttons on one fixed row wanted 437px — wider than a narrow
        # tray ever is, so the card could only hang off the edge. They wrap
        # now. The gap that used to separate the gripper's three from the
        # camera's two cannot survive wrapping (a flow has no stretch), so
        # the order carries the grouping instead: gripper, then the rest.
        row = FlowBar(spacing=0, line_spacing=ROW_PADDING)
        row.add(control_button(
            "Grab", lambda: live.gripper("GRAB"),
            enabled=enabled, primary=True,
        ))
        row.add(control_button(
            "Soft hold", lambda: live.gripper("SOFTHOLD"), enabled=enabled,
        ))
        row.add(control_button(
            "Drop", lambda: live.gripper("DROP"), enabled=enabled,
        ))
        row.add(control_button("Perch", live.perch, enabled=enabled))
        row.add(control_button("Take photo", live.photo, enabled=enabled))
        self.layout().addWidget(row)


class ManualControl(QWidget):
    """The tray's Manual Control tab: a target picker and the controls.

    Rebuilt rather than updated when the selection changes. Every control's
    enabled state, the topic label, and the picker's own contents all follow
    from one fact — whether a robot is marked — so rebuilding is both the
    smaller code and the one that cannot leave a stale corner behind. It is
    a handful of widgets, built only when the tray is opened on this tab.

    The service reports back through two callbacks: `announce` for what just
    happened, which lands in this tab's own status line, and `refresh` for a
    selection that changed, which rebuilds. A workspace routed those to the
    window's status strip; a tray tab is not on screen with the strip and
    says it here instead.
    """

    def __init__(self) -> None:
        super().__init__()
        self.live = LiveActions(announce=self.say, refresh=self.schedule_rebuild)
        self._known_robots = self.live.robot_snapshot()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 0)
        layout.setSpacing(5)

        header = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName("DebugStatus")
        header.addWidget(self.status)
        header.addStretch(1)
        layout.addLayout(header)

        # The controls scroll: the tray is a short dock the user resizes, and
        # two columns of cards do not always fit in the height they gave it.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        layout.addWidget(self._scroll, 1)

        self.rebuild()

    def say(self, message: str) -> None:
        if message:
            self.status.setText(message)

    def schedule_rebuild(self) -> None:
        """Rebuild once the signal that asked for it has finished.

        The picker is inside the controls it rebuilds: choosing a robot
        emits from a combo box that the rebuild then destroys, and Qt is
        still inside that widget's own signal when it happens — which
        segfaults rather than raising. Deferring to the next turn of the
        event loop lets the emitting widget finish before it is replaced.
        """
        QTimer.singleShot(0, self.rebuild)

    def rebuild(self) -> None:
        """Rebuild the controls for the currently selected robot."""
        enabled = bool(self.live.robot)
        self._scroll.setWidget(self._controls(enabled))
        if not enabled:
            self.status.setText("No robot marked")
        elif not self.status.text() or self.status.text() == "No robot marked":
            robot = self.live.selected_robot
            name = robot.display_name if robot is not None else self.live.robot
            self.status.setText(f"{name} · publishing as Studio")

    def _controls(self, enabled: bool) -> QWidget:
        """The cards, flowed onto as many rows as the width needs.

        Two fixed columns was the workspace's layout, and a workspace has a
        whole window to fill. A tray is whatever height the user dragged it
        to and whatever width the window happens to be, so the cards wrap
        instead: four across on a wide window, two, or one, without ever
        putting a control past the right edge where nothing can reach it.
        """
        # The cards carry no description paragraph, unlike the same cards on
        # a page. A sentence explaining that Drag previews and release sends
        # is worth a line on a screen you arrive at to read; in a dock a few
        # hundred pixels tall it is the line that pushes the slider itself
        # out of sight. The titles and the controls say it, and the sentence
        # is on the card as a tooltip for anyone who wants it.
        cards = FlowBar(spacing=CARD_GAP, line_spacing=CARD_GAP)
        for card in (
            TargetCard(self.live),
            JointCard(self.live, enabled=enabled),
            ReachCard(self.live, enabled=enabled),
            BaseCard(self.live, enabled=enabled),
            QuickActionsCard(self.live, enabled=enabled),
        ):
            # Cards are built to fill a page column, which in a tray would
            # make one card as wide as the tray and the rest wrap under it.
            # A ceiling keeps them to a readable measure and lets several
            # share a row; the floor is what the sliders inside actually
            # need, so shrinking never clips a control.
            card.setMinimumWidth(CARD_MIN_WIDTH)
            card.setMaximumWidth(CARD_MAX_WIDTH)
            cards.add(card)

        if not enabled:
            note = Card(
                "No robot available",
                "Create an MQTT user and mark it as a robot on Network → "
                "Robots. The controls will enable when you come back.",
            )
            note.setMinimumWidth(CARD_MIN_WIDTH)
            note.setMaximumWidth(CARD_MAX_WIDTH)
            cards.add(note)

        body = Column(cards)
        body.layout().addStretch(1)
        return body

    def refresh(self) -> None:
        """Catch up with robots added, removed, or renamed elsewhere.

        The tray calls this on its timer. Cheap when nothing moved — a
        snapshot compare — and the only thing that can change this tab from
        outside is the robot registry.
        """
        if self.live.robots_changed():
            self.rebuild()
