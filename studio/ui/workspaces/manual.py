"""Manual Controller — direct arm, base, gripper, and camera control."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...models.config.robots import robots
from ...services.network import mqtt_client
from ...services.network.pub_sub import manual_messages
from ..components import ActionSpec, Card, Column, Separator
from ..pages.base import Page
from ..theme.metrics import CARD_GAP, CARD_SPACING, ROW_PADDING
from .base import Workspace


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
    def __init__(self, workspace: "ManualWorkspace") -> None:
        super().__init__(
            "Target robot",
            "Commands publish on the selected robot's /test topic through "
            "Studio's configured MQTT account.",
        )

        available = workspace.robots()
        row = QHBoxLayout()
        row.setContentsMargins(0, CARD_GAP, 0, 0)
        row.setSpacing(ROW_PADDING)

        label = QLabel("Robot")
        label.setObjectName("ManualControlLabel")
        row.addWidget(label)

        self.combo = QComboBox()
        self.combo.setAccessibleName("Target robot")
        if available:
            names = [robot.name for robot in available]
            self.combo.addItems([robot.display_name for robot in available])
            if workspace.robot in names:
                self.combo.setCurrentIndex(names.index(workspace.robot))
            self.combo.currentIndexChanged.connect(
                lambda index: workspace.select_robot(names[index])
            )
        else:
            self.combo.addItem("No robots marked")
            self.combo.setEnabled(False)
        row.addWidget(self.combo, 1)

        topic = QLabel(
            f"{workspace.robot}/test" if workspace.robot else "Network → Robots"
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

    def __init__(self, workspace: "ManualWorkspace", *, enabled: bool) -> None:
        super().__init__(
            "Arm joints",
            "Drag to preview; release to send the angle to the robot.",
        )
        self.controls: dict[str, SliderControl] = {}
        for label, joint, default in self.JOINTS:
            control = SliderControl(
                label,
                0,
                180,
                default,
                "°",
                enabled=enabled,
                on_commit=lambda value, name=joint: workspace.servo(name, value),
            )
            self.controls[joint] = control
            self.layout().addSpacing(CARD_GAP)
            self.layout().addWidget(control)


class ReachCard(Card):
    def __init__(self, workspace: "ManualWorkspace", *, enabled: bool) -> None:
        super().__init__(
            "Reach",
            "Move with the robot's calibrated inverse kinematics.",
        )
        self.control = SliderControl(
            "Distance",
            1,
            120,
            60,
            " mm",
            enabled=enabled,
            on_commit=workspace.reach,
        )
        self.layout().addSpacing(CARD_GAP)
        self.layout().addWidget(self.control)


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

    def __init__(self, workspace: "ManualWorkspace", *, enabled: bool) -> None:
        super().__init__(
            "Base rotation",
            "Move in encoder steps. 216 steps is one full turn.",
        )

        settings = QHBoxLayout()
        settings.setContentsMargins(0, CARD_GAP, 0, 0)
        settings.setSpacing(ROW_PADDING)

        settings.addWidget(QLabel("Steps"))
        self.steps = QSpinBox()
        self.steps.setAccessibleName("Base steps")
        self.steps.setRange(1, 216)
        self.steps.setValue(1)
        self.steps.setEnabled(enabled)
        settings.addWidget(self.steps)

        settings.addWidget(QLabel("Speed"))
        self.speed = QComboBox()
        self.speed.setAccessibleName("Base speed")
        for label, value in self.SPEEDS:
            self.speed.addItem(label, value)
        self.speed.setCurrentIndex(1)
        self.speed.setEnabled(enabled)
        settings.addWidget(self.speed, 1)
        self.layout().addLayout(settings)

        buttons = QHBoxLayout()
        buttons.setSpacing(0)
        buttons.addWidget(control_button(
            "← Left",
            lambda: workspace.rotate(
                "LEFT", self.steps.value(), str(self.speed.currentData())
            ),
            enabled=enabled,
        ))
        buttons.addWidget(control_button(
            "Right →",
            lambda: workspace.rotate(
                "RIGHT", self.steps.value(), str(self.speed.currentData())
            ),
            enabled=enabled,
        ))
        buttons.addWidget(control_button(
            "Home base",
            workspace.home,
            enabled=enabled,
        ))
        self.layout().addSpacing(CARD_GAP)
        self.layout().addLayout(buttons)


class QuickActionsCard(Card):
    def __init__(self, workspace: "ManualWorkspace", *, enabled: bool) -> None:
        super().__init__(
            "Gripper & camera",
            "The same quick actions from the original live controller.",
        )
        row = QHBoxLayout()
        row.setContentsMargins(0, CARD_GAP, 0, 0)
        row.setSpacing(0)
        row.addWidget(control_button(
            "Grab", lambda: workspace.gripper("GRAB"),
            enabled=enabled, primary=True,
        ))
        row.addWidget(control_button(
            "Soft hold", lambda: workspace.gripper("SOFTHOLD"), enabled=enabled,
        ))
        row.addWidget(control_button(
            "Drop", lambda: workspace.gripper("DROP"), enabled=enabled,
        ))
        row.addStretch(1)
        row.addWidget(control_button("Perch", workspace.perch, enabled=enabled))
        row.addWidget(control_button("Take photo", workspace.photo, enabled=enabled))
        self.layout().addLayout(row)


class DrivePage(Page):
    key = "drive"
    label = "Drive"

    title = "Manual Controller"
    subtitle = "Live control for joints, reach, base, gripper, and camera."

    @property
    def status(self) -> str:
        robot = self.workspace.selected_robot
        if robot is None:
            return "No robot selected"
        return f"{robot.display_name} · publishing as Studio"

    def build_page(self) -> QWidget:
        enabled = bool(self.workspace.robot)

        left = Column(
            JointCard(self.workspace, enabled=enabled),
            ReachCard(self.workspace, enabled=enabled),
        )
        right = Column(
            BaseCard(self.workspace, enabled=enabled),
            QuickActionsCard(self.workspace, enabled=enabled),
        )
        right.layout().addStretch(1)

        surface = QWidget()
        layout = QHBoxLayout(surface)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(CARD_GAP)
        layout.addWidget(left, 1)
        layout.addWidget(right, 1)

        sections = [TargetCard(self.workspace)]
        if not enabled:
            sections.append(Card(
                "No robot available",
                "Create an MQTT user and mark it as a robot on Network → "
                "Robots. The controller will enable when you return here.",
            ))
        sections.append(surface)
        return Column(*sections)


class ManualWorkspace(Workspace):
    key = "manual"
    label = "Manual"
    page_classes = [DrivePage]

    def __init__(self) -> None:
        super().__init__()
        self._robot = ""
        self._known_robots = self._robot_snapshot()

    def robots(self) -> list:
        return robots().all()

    def _robot_snapshot(self) -> tuple[tuple[str, str], ...]:
        return tuple((robot.name, robot.label) for robot in self.robots())

    @property
    def selected_robot(self):
        return robots().find(self.robot) if self.robot else None

    @property
    def robot(self) -> str:
        if self._robot and robots().find(self._robot) is not None:
            return self._robot
        available = self.robots()
        return available[0].name if available else ""

    def select_robot(self, name: str) -> None:
        if name == self.robot:
            return
        self._robot = name
        self.refresh()

    def enter(self) -> None:
        """Refresh if robots were added, removed, or renamed elsewhere."""
        snapshot = self._robot_snapshot()
        if snapshot != self._known_robots:
            self._known_robots = snapshot
            self.refresh()

    def client(self):
        client = mqtt_client()
        client.reconcile()
        return client

    def _publish(self, sender, description: str, *args) -> str:
        robot = self.robot
        if not robot:
            self.say("Mark a user as a robot on Network → Robots first.")
            return ""

        client = self.client()
        if not client.running:
            self.say(client.status)
            return ""

        action_id = sender(client, robot, *args)
        target = self.selected_robot
        self.say(
            f"Sent {description} to "
            f"{target.display_name if target is not None else robot} as Studio."
        )
        return action_id

    def servo(self, joint: str, position: int) -> str:
        return self._publish(
            manual_messages.send_servo, f"{joint.title()} {position}°", joint, position
        )

    def reach(self, distance: int) -> str:
        return self._publish(
            manual_messages.send_ik, f"reach {distance} mm", distance
        )

    def rotate(self, direction: str, steps: int, speed: str) -> str:
        return self._publish(
            manual_messages.send_base_steps,
            f"base {direction.lower()} {steps} step{'s' if steps != 1 else ''}",
            direction,
            steps,
            speed,
        )

    def home(self) -> str:
        return self._publish(manual_messages.send_base_home, "base home")

    def gripper(self, command: str) -> str:
        return self._publish(
            manual_messages.send_gripper,
            command.lower().replace("softhold", "soft hold"),
            command,
        )

    def perch(self) -> str:
        return self._publish(manual_messages.send_perch, "perch")

    def photo(self) -> str:
        return self._publish(manual_messages.send_photo, "photo request")

    def build_actions(self) -> list:
        enabled = bool(self.robot)
        return [
            ActionSpec("Home Base", on_click=self.home if enabled else None),
            ActionSpec("Perch", on_click=self.perch if enabled else None),
            Separator(),
            ActionSpec(
                "Grab", primary=True,
                on_click=(lambda: self.gripper("GRAB")) if enabled else None,
            ),
            ActionSpec(
                "Soft Hold",
                on_click=(lambda: self.gripper("SOFTHOLD")) if enabled else None,
            ),
            ActionSpec(
                "Drop", on_click=(lambda: self.gripper("DROP")) if enabled else None,
            ),
            Separator(),
            ActionSpec("Photo", on_click=self.photo if enabled else None),
        ]
