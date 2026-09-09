"""The Manual Controller's commands, without a window around them.

The Manual Controller is a tray tab of sliders and buttons, but very little
of what it does is about sliders and buttons. Underneath the widgets sits one
question asked eight ways — *send this command to the robot that is selected,
if there is one and the broker is up* — and that question has nothing to do
with Qt. It is the same question a script, a test, or a future headless mode
would ask.

So it lives here. `LiveActions` owns which robot is selected, gets Studio's
authenticated client, and refuses to publish when it cannot: no robot marked,
or a broker that is not running. Each command returns the action id the robot
will echo back, or `""` for a command that was never sent — the caller can
tell the difference without catching anything.

Two things are handed in rather than reached for, because both are the
caller's business and not this object's:

    announce    one line for the user about what just happened. The tray tab
                puts it in its own status line; a test collects it in a list;
                a script prints it or ignores it entirely.
    refresh     that the selected robot changed, so whatever is drawing the
                controller can redraw. The service does not know that a
                selection has a picker showing it.

What stays in the UI is what is genuinely about the screen: the widgets, when
a control is enabled, and how a robot's name is spelled in a dropdown.
"""

from __future__ import annotations

from collections.abc import Callable

from ...models.config.robots import robots
from ..network import mqtt_client
from ..network.pub_sub import manual_messages


class LiveActions:
    """Selected robot, plus the commands that can be sent to it."""

    def __init__(
        self,
        *,
        announce: Callable[[str], None] | None = None,
        refresh: Callable[[], None] | None = None,
    ) -> None:
        self._robot = ""
        self._announce = announce
        self._refresh = refresh
        self._known_robots = self.robot_snapshot()

    # ---- which robot ------------------------------------------------------
    def robots(self) -> list:
        return robots().all()

    def robot_snapshot(self) -> tuple[tuple[str, str], ...]:
        """Enough of the registry to notice it changed elsewhere.

        Name and label both: a robot renamed is a robot the picker is
        currently spelling wrong, which matters as much as one added.
        """
        return tuple((robot.name, robot.label) for robot in self.robots())

    @property
    def robot(self) -> str:
        """The robot commands go to, as a name.

        Falls back to the first available rather than holding a selection
        that no longer exists: a robot can be deleted on Network → Robots
        while this controller is open, and a stale name would publish to a
        topic nothing is listening on.
        """
        if self._robot and robots().find(self._robot) is not None:
            return self._robot
        available = self.robots()
        return available[0].name if available else ""

    @property
    def selected_robot(self):
        """The full record, for anything that needs more than the name."""
        return robots().find(self.robot) if self.robot else None

    def select_robot(self, name: str) -> None:
        if name == self.robot:
            return
        self._robot = name
        self.changed()

    def robots_changed(self) -> bool:
        """Whether the registry moved since this was last asked.

        The controller is not the only place robots are edited. Asking on
        the way in is cheaper than subscribing, and this is the object that
        knows what it last saw.
        """
        snapshot = self.robot_snapshot()
        if snapshot == self._known_robots:
            return False
        self._known_robots = snapshot
        return True

    # ---- talking to the caller -------------------------------------------
    def say(self, message: str) -> None:
        if self._announce is not None and message:
            self._announce(message)

    def changed(self) -> None:
        if self._refresh is not None:
            self._refresh()

    # ---- publishing -------------------------------------------------------
    def client(self):
        """Studio's own MQTT client, reconciled before use.

        The Manual Controller publishes as Studio rather than as the robot,
        so it wants the shared client rather than one of its own; the
        reconcile is what makes an account edited elsewhere take effect here.
        """
        client = mqtt_client()
        client.reconcile()
        return client

    def publish(self, sender, description: str, *args) -> str:
        """Send one command, or say why it could not be sent.

        Returns the action id the robot will quote in its reply, and `""`
        for a command that never left — the two refusals here are both
        ordinary states of the app rather than errors, so they are said to
        the user rather than raised.
        """
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

    # ---- the commands -----------------------------------------------------
    def servo(self, joint: str, position: int) -> str:
        return self.publish(
            manual_messages.send_servo, f"{joint.title()} {position}°", joint, position
        )

    def reach(self, distance: int) -> str:
        return self.publish(
            manual_messages.send_ik, f"reach {distance} mm", distance
        )

    def rotate(self, direction: str, steps: int, speed: str) -> str:
        return self.publish(
            manual_messages.send_base_steps,
            f"base {direction.lower()} {steps} step{'s' if steps != 1 else ''}",
            direction,
            steps,
            speed,
        )

    def home(self) -> str:
        return self.publish(manual_messages.send_base_home, "base home")

    def gripper(self, command: str) -> str:
        return self.publish(
            manual_messages.send_gripper,
            command.lower().replace("softhold", "soft hold"),
            command,
        )

    def perch(self) -> str:
        return self.publish(manual_messages.send_perch, "perch")

    def photo(self) -> str:
        return self.publish(manual_messages.send_photo, "photo request")
