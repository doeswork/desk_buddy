"""Running the open workflow, and showing how far it got.

`services.workflows.runner` owns the protocol — one step in flight, correlate
on action_id, retry, time out, stop on failure. None of that is repeated here.
What this adds is the three things a service deliberately does not have:

    a thread     replies arrive on paho's network thread, and every one of
                 them ends in a widget update
    a clock      retry and timeout only happen when someone calls `tick()`
    a robot      the run publishes to whichever robot Studio is pointed at

The thread part is not a formality. Updating widgets from the MQTT thread is
what segfaulted a calibration run mid-layout, so status snapshots cross into
the GUI thread through a queued signal exactly as `calibration/step.py` does.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from PySide6.QtWidgets import QWidget

from ....models.config.current_robot import current_robot
from ....services.network import mqtt_client
from ....services.network.pub_sub.robot_topics import (
    heartbeat_topic,
    photo_topic,
    vision_topic,
)
from ....services.workflows.runner.run import Run, RunStatus
from ...components import Card

# How often to give the run a chance to retry or time out. The runner does no
# waiting of its own — `tick()` is what moves those clocks — and a second is
# fine for deadlines measured in seconds.
TICK_MS = 1000


class RunBridge(QObject):
    """Carries a status snapshot from the network thread to the GUI thread.

    Same rule, and the same reason, as `calibration.step.ReplyBridge`: a
    signal emitted on one thread to a receiver living on another is delivered
    by Qt as a queued connection, which is the one supported way across.
    """

    arrived = Signal(object)
    # The three things the live view watches. All of them arrive on paho's
    # thread and all of them end in a repaint, so all of them cross here.
    heartbeat = Signal(dict)
    photo = Signal(bytes)
    vision = Signal(dict)


class WorkflowRunner(QObject):
    """One workflow run at a time, on behalf of the Workflows workspace."""

    changed = Signal()
    # Forwarded to the live view, already on the GUI thread.
    heartbeat = Signal(dict)
    photo = Signal(bytes)
    vision = Signal(dict)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._run: Run | None = None
        self._status = RunStatus()
        self._workflow = ""
        self._robot = ""
        self._unsubscribe = None
        # The live view's own subscriptions, separate from the run's: they
        # start with the run but a heartbeat is not a step reply, and the
        # runner must not see traffic it did not ask for.
        self._watching: list = []

        self._bridge = RunBridge(self)
        self._bridge.arrived.connect(self._on_status, Qt.QueuedConnection)
        self._bridge.heartbeat.connect(self.heartbeat, Qt.QueuedConnection)
        self._bridge.photo.connect(self.photo, Qt.QueuedConnection)
        self._bridge.vision.connect(self.vision, Qt.QueuedConnection)

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    # ---- what the page reads ---------------------------------------------
    @property
    def status(self) -> RunStatus:
        return self._status

    @property
    def workflow(self) -> str:
        """The name of the workflow this status belongs to."""
        return self._workflow

    @property
    def robot(self) -> str:
        """The robot the run is addressed to."""
        return self._robot

    @property
    def running(self) -> bool:
        return self._status.running

    # ---- starting and stopping -------------------------------------------
    def start(self, workflow) -> str:
        """Run `workflow`. Returns "" or why it could not start.

        Refusals are ordinary states of the app rather than errors: no robot
        marked, an empty workflow, or a run already going. Each is something
        the user can see and fix, so each is said rather than raised.
        """
        if self.running:
            return "A workflow is already running."
        if not workflow.steps:
            return "This workflow has no steps to run."

        robot = current_robot().name
        if not robot:
            return (
                "No robot selected. Mark one on Network → Robots, then pick "
                "it in Calibration."
            )

        client = mqtt_client()
        client.reconcile()

        self._run = Run(client, robot, list(workflow.steps))
        self._workflow = workflow.name
        self._robot = robot
        # Subscribed before `start()` publishes anything, so a snapshot
        # cannot be missed between the two.
        self._unsubscribe = self._run.on_change(self._bridge.arrived.emit)

        # Watched for the live view only. Raw photo bytes need `subscribe_raw`
        # — a binary frame is not JSON and decoding it as such would drop it.
        # The previous run's watchers are dropped here rather than when it
        # ended, so its last verdict had time to arrive.
        self._stop_watching()
        self._watching = [
            client.subscribe(
                heartbeat_topic(robot),
                lambda _topic, body: self._bridge.heartbeat.emit(body),
            ),
            client.subscribe_raw(
                photo_topic(robot),
                lambda _topic, payload: self._bridge.photo.emit(payload),
            ),
            client.subscribe(
                vision_topic(robot),
                lambda _topic, body: self._bridge.vision.emit(body),
            ),
        ]

        if not self._run.start():
            problem = self._run.status.message or "The run could not start."
            self._release()
            return problem

        self._timer.start()
        self.changed.emit()
        return ""

    def stop(self) -> None:
        if self._run is not None:
            self._run.stop("Stopped.")

    # ---- the clock and the replies ---------------------------------------
    def _tick(self) -> None:
        if self._run is not None:
            self._run.tick()

    def _on_status(self, status: RunStatus) -> None:
        """A snapshot, now on the GUI thread where widgets are legal."""
        self._status = status
        if not status.running:
            self._timer.stop()
            self._release()
        self.changed.emit()

    def _release(self) -> None:
        """Stop running. The live view keeps watching.

        Only the run's own subscription goes: the detector answers *after*
        the firmware's terminal reply — a full second later in a captured
        run — so tearing the watchers down here would drop the verdict for
        the very last step, which is the one the user is about to read.
        They are replaced on the next run and outlive this one.
        """
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._run = None

    def _stop_watching(self) -> None:
        """Drop the live view's subscriptions, before taking new ones."""
        for unsubscribe in self._watching:
            unsubscribe()
        self._watching = []


# How each step's status reads on screen. The runner's own words are states
# in a volley; these are what happened.
STEP_WORDS = {
    "waiting": "queued",
    "pending": "running…",
    "complete": "done",
    "failed": "failed",
    "timed_out": "no reply",
    "stopped": "stopped",
}


def run_card(runner: WorkflowRunner) -> QWidget | None:
    """What the run is doing, or None when there is nothing to report.

    Every step is listed, not only the failed one: a run that stopped at step
    four is a question about steps one to three as much as four, and the
    world the later steps assumed is the one the earlier ones left behind.
    """
    status = runner.status
    if status.state == "idle" or not status.events:
        return None

    lines = []
    for event in status.events:
        word = STEP_WORDS.get(event.status, event.status)
        line = f"{event.position + 1}. {event.subject} — {word}"
        if event.problem:
            line += f": {event.problem}"
        lines.append(line)

    heading = {
        "running": f"Running {runner.workflow} on {runner.robot}",
        "complete": f"{runner.workflow} finished",
        "failed": f"{runner.workflow} stopped",
        "stopped": f"{runner.workflow} stopped",
    }.get(status.state, runner.workflow)

    body = "\n".join(lines)
    if status.message:
        body = f"{status.message}\n\n{body}"
    return Card(heading, body)
