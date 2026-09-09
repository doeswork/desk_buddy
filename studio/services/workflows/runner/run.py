"""Running a workflow: the send / pending / complete volley, one step at a time.

`models.config.workflows` stores what a workflow *is*. This runs one — it
publishes a step's command to the robot, waits for the firmware's terminal
reply on the same topic, and advances to the next step only once that reply
arrives.

The shape is the useful half of the Rails `StepEvent` model this replaces.
There, each step was a database row whose `status` column moved
pending -> complete, with `after_update_commit :create_next_action` chaining
the next row, a NOTIFY to push the MQTT message, and two background jobs per
row for retry and timeout. Studio has no database and no job queue, so the
chain is held here instead: a list of :class:`StepEvent` records and an index,
advanced by the reply handler. What carries over is the part that was really
the protocol rather than the persistence:

* One step in flight at a time. §1 of `firmware/MQTT_SPEC.md` is explicit that
  the ESP32 dispatches synchronously from a single-message slot -- "Send one
  stateful command at a time and wait for its exact matching terminal
  response." Rails enforced the same rule with a `only_one_pending_action`
  validation; here it is simply that `_pending` holds one action_id.
* Correlate strictly on `action_id`. Commands and replies share one topic
  (`{robot}/test`) with heartbeats, ready messages and debug traffic, so a
  reply is ours only if its id matches the step we are waiting on.
* A failed step stops the run. Rails returned early from `create_next_action`
  on "failed"; the same reasoning applies harder here, because the steps after
  a missed grab are operating on a world that is not where they think it is.
* Retry, then time out. The firmware's no-response matrix (§10) lists several
  situations that produce *no* terminal reply at all -- an oversized command, a
  `calibrate` parse failure, a photo publish failure. Without a timeout those
  hang the run forever.

Deliberately not carried over: sub-steps. Rails grew `parent_step_event_id`,
`sub_step_position`, and instruction logs so a vision "pick up" could expand
into a generated child sequence. Nothing in Studio produces those instructions
yet, and inventing the schema before the producer exists would be guessing at
it. The runner is written so that expansion is an addition rather than a
rewrite -- see :meth:`Run._advance`.

No Qt in here, per `services/`. The reply callback arrives on paho's network
thread; a GUI caller must cross a queued signal before touching widgets, the
same way `ui/workspaces/calibration/step.py` does with its `ReplyBridge`.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Callable

# Studio identifies itself as the sender on every command it publishes.
# Never "firmware" -- §2 says the firmware ignores messages claiming that.
SENDER = "studio"

# Envelope fields belong to the exchange, not to the step's parameters. A
# step dictionary carrying one of these does not get to override the
# runner's own bookkeeping.
RESERVED = ("sender", "action_id", "action", "status")

# The Rails-era subject that was not a firmware action name. `map_subject_to_action`
# existed to translate exactly this one; the step palette now writes
# `baseRotate` directly, but hand-written and older workflow files still say
# `rotate`, and silently publishing an unknown action would get no reply at
# all rather than an error worth reading.
SUBJECT_ALIASES = {"rotate": "baseRotate"}

# Photo actions never publish `completed` (§6). Their sequence is
# in_progress -> binary frame -> in_progress with log:"sent", and that second
# in_progress is the only terminal signal there is. It is emitted even when
# the capture failed, so it means "the robot is done with this step", not
# "a valid JPEG arrived" -- which is the honest thing for a runner to wait on.
PHOTO_ACTIONS = ("photo", "detect_object", "detect_color", "calibrate_depth")

# How long to wait for a terminal reply before giving up on a step.
STEP_TIMEOUT = 60.0

# Re-publish an unanswered command this long after sending. The single-message
# receive slot means a command that arrived while the previous action was
# still blocking dispatch can be dropped, and a resend is cheap: the firmware
# correlates on action_id, so a duplicate that *did* land produces a duplicate
# in_progress rather than a second movement.
RETRY_DELAYS = (1.0, 5.0)


def action_for(subject: str) -> str:
    """The firmware `action` name a step's `subject` publishes as."""
    return SUBJECT_ALIASES.get(subject, subject)


def topic_for(robot: str) -> str:
    """Commands and replies share one topic, per §1."""
    return f"{robot}/test"


def message_for(step: dict[str, Any], action_id: str) -> dict[str, Any]:
    """The MQTT command envelope for one workflow step.

    Every key of the step except `subject` is a firmware parameter and is
    copied through untouched: `models.config.workflows` deliberately keeps
    step dictionaries open-ended so a custom action still round-trips, and
    the runner has no business being stricter than the file format.
    """
    body = {
        key: value
        for key, value in step.items()
        if key != "subject" and key not in RESERVED
    }
    body["sender"] = SENDER
    body["action_id"] = action_id
    body["action"] = action_for(str(step.get("subject", "")))
    return body


def is_terminal(action: str, payload: dict[str, Any]) -> bool:
    """Whether `payload` ends the step -- successfully or not.

    Two shapes, because the firmware has two. Ordinary actions finish on
    `completed`/`failed`. Photo actions have no `completed` at all and finish
    on their second `in_progress`, the one carrying `log:"sent"` (§6).
    """
    status = payload.get("status")
    if action in PHOTO_ACTIONS:
        return status == "in_progress" and payload.get("log") == "sent"
    return status in ("completed", "failed")


def succeeded(action: str, payload: dict[str, Any]) -> bool:
    """Whether a terminal reply reports success.

    A photo's terminal `in_progress` is published even when the capture or
    the publish failed, so it can only be read as "the robot finished", never
    as proof of a picture. Treating it as success is the accurate reading of
    what the firmware actually tells us.
    """
    if action in PHOTO_ACTIONS:
        return True
    return payload.get("status") == "completed"


def problem_from(payload: dict[str, Any]) -> str:
    """The most useful failure text a reply carries.

    Detailed base and stencil responses nest their reason under the result
    key rather than at the top level (§3), so a top-level-only reading would
    report an empty error for exactly the failures worth reading.
    """
    for key in ("error", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    for value in payload.values():
        if isinstance(value, dict):
            nested = value.get("error") or value.get("message")
            if isinstance(nested, str) and nested:
                return nested
    return "The robot reported a failure."


@dataclass(frozen=True)
class StepEvent:
    """One step's trip to the robot and back.

    The fields Rails kept in a `step_events` row, minus the persistence: what
    was sent, what came back, and where in the volley it currently is.
    """

    position: int
    subject: str
    step: dict[str, Any]
    status: str = "waiting"       # waiting -> pending -> complete/failed/timed_out/stopped
    action_id: str = ""
    sent_at: float = 0.0
    reply: dict[str, Any] = field(default_factory=dict)
    problem: str = ""

    @property
    def action(self) -> str:
        return action_for(self.subject)

    @property
    def finished(self) -> bool:
        return self.status in ("complete", "failed", "timed_out", "stopped")


@dataclass(frozen=True)
class RunStatus:
    """A snapshot of the run, safe to read from another thread."""

    state: str = "idle"           # idle -> running -> complete/failed/stopped
    message: str = ""
    events: tuple[StepEvent, ...] = ()
    position: int = 0

    @property
    def running(self) -> bool:
        return self.state == "running"

    @property
    def total(self) -> int:
        return len(self.events)

    @property
    def current(self) -> StepEvent | None:
        for event in self.events:
            if event.status == "pending":
                return event
        return None


class Run:
    """One workflow, executing against one robot.

    Created stopped. :meth:`start` publishes the first step; each terminal
    reply publishes the next. The caller drives timeouts and retries by
    calling :meth:`tick` on a timer -- there is no thread here, because the
    replies already arrive on paho's, and adding a second one would mean
    two threads mutating the same chain.
    """

    def __init__(
        self,
        client,
        robot: str,
        steps,
        *,
        timeout: float = STEP_TIMEOUT,
        retry_delays: tuple[float, ...] = RETRY_DELAYS,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._robot = robot
        self._timeout = timeout
        self._retry_delays = tuple(retry_delays)
        self._now = now
        # Replies arrive on paho's network thread while the GUI thread reads
        # status and may call stop(); every mutation below is under this lock.
        self._lock = threading.Lock()
        self._events = [
            StepEvent(position=index, subject=str(step.get("subject", "")), step=dict(step))
            for index, step in enumerate(steps)
        ]
        self._state = "idle"
        self._message = "Not started."
        # The action_id of the step in flight, or "" when nothing is pending.
        # One at a time, per §1.
        self._pending = ""
        self._retries = 0
        self._unsubscribe = None
        self._listeners: list[Callable[[RunStatus], None]] = []
        # A robot on loopback can reply from inside publish(), so _send ->
        # _publish -> _on_reply -> _advance -> _send is a real cycle rather
        # than a theoretical one. These turn it into a loop: the outermost
        # _send drains, and a re-entrant one only leaves the next position
        # behind. Without this a workflow recursed one frame per step and
        # blew the stack partway through a long routine.
        self._sending = False
        self._queued: int | None = None

    # ---- reading ---------------------------------------------------------
    @property
    def status(self) -> RunStatus:
        with self._lock:
            return self._snapshot()

    def _snapshot(self) -> RunStatus:
        """Caller holds the lock."""
        position = sum(1 for event in self._events if event.finished)
        return RunStatus(self._state, self._message, tuple(self._events), position)

    def on_change(self, listener: Callable[[RunStatus], None]) -> Callable[[], None]:
        """Call `listener(status)` whenever the run advances.

        **Listeners run on paho's network thread.** A UI listener must not
        touch a widget from here -- hand the status over with a queued signal
        first, the way `ReplyBridge` does in the calibration workspace.
        """
        with self._lock:
            self._listeners.append(listener)

        def remove() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return remove

    def _notify(self, status: RunStatus) -> None:
        """Called without the lock held, so a listener can read status."""
        for listener in list(self._listeners):
            try:
                listener(status)
            except Exception:  # noqa: BLE001 - one bad listener must not halt a run
                pass

    # ---- running ---------------------------------------------------------
    def start(self) -> bool:
        """Subscribe, publish the first step, and begin waiting."""
        with self._lock:
            if self._state == "running":
                return False
            if not self._robot:
                self._state, self._message = "failed", "No robot is selected."
                status = self._snapshot()
            elif not self._events:
                self._state, self._message = "complete", "This workflow has no steps."
                status = self._snapshot()
            else:
                self._state = "running"
                self._message = "Running…"
                status = None

        if status is not None:
            self._notify(status)
            return False

        # Subscribed before the first publish: a fast robot can reply before
        # a subscription taken afterwards would exist, and that reply is the
        # one that advances the run.
        self._unsubscribe = self._client.subscribe(topic_for(self._robot), self._on_reply)
        self._send(0)
        return True

    def stop(self, message: str = "Stopped.") -> None:
        """Give up on the run. The robot finishes whatever it is mid-way through.

        There is no firmware "cancel" for an arbitrary action -- handlers are
        synchronous (§1) -- so this stops Studio listening and advancing. It
        does not claim to have stopped the arm.
        """
        with self._lock:
            if self._state not in ("running", "idle"):
                return
            self._state = "stopped"
            self._message = message
            self._pending = ""
            self._events = [
                replace(event, status="stopped") if event.status == "pending" else event
                for event in self._events
            ]
            status = self._snapshot()
        self._release()
        self._notify(status)

    def tick(self) -> None:
        """Drive retries and the timeout. Call periodically while running.

        Kept as a poll rather than a timer thread because the retry and
        timeout deadlines only matter relative to the step in flight, and the
        caller already has a timer -- the network workspace polls its
        coordinators exactly this way.
        """
        resend: int | None = None
        status: RunStatus | None = None
        with self._lock:
            if self._state != "running" or not self._pending:
                return
            event = self._current_event()
            if event is None:
                return
            waited = self._now() - event.sent_at
            if waited >= self._timeout:
                self._events[event.position] = replace(
                    event,
                    status="timed_out",
                    problem=(
                        f"No reply after {self._timeout:.0f}s. The robot may be "
                        "offline, or the command may have been dropped."
                    ),
                )
                self._pending = ""
                self._state = "failed"
                self._message = f"Step {event.position + 1} timed out."
                status = self._snapshot()
            elif (
                self._retries < len(self._retry_delays)
                and waited >= self._retry_delays[self._retries]
            ):
                self._retries += 1
                resend = event.position
            else:
                return

        if resend is not None:
            self._publish(resend)
            return
        self._release()
        self._notify(status)

    # ---- the volley ------------------------------------------------------
    def _send(self, position: int) -> None:
        """Mark step `position` pending and publish it.

        Iterative rather than recursive: a reply delivered from inside
        publish() re-enters here for the next step, and nesting one frame per
        step would exhaust the stack on a long workflow.
        """
        with self._lock:
            if self._sending:
                # An outer _send is already draining; hand it the next step.
                self._queued = position
                return
            self._sending = True

        try:
            while True:
                with self._lock:
                    if self._state != "running":
                        return
                    self._queued = None
                    event = self._events[position]
                    action_id = uuid.uuid4().hex
                    self._events[position] = replace(
                        event, status="pending", action_id=action_id,
                        sent_at=self._now(),
                    )
                    self._pending = action_id
                    self._retries = 0
                    self._message = (
                        f"Step {position + 1} of {len(self._events)}: {event.subject}"
                    )
                    status = self._snapshot()

                # Announce "pending" before the command goes out, not after. A
                # reply can arrive on the network thread from inside publish()
                # and run this step's completion -- and the next step, and the
                # end of the run -- before publish() returns. Notifying
                # afterwards would deliver those states to listeners in
                # reverse, so a finished run would render as still running.
                self._notify(status)
                self._publish(position)

                with self._lock:
                    if self._queued is None:
                        return
                    position = self._queued
        finally:
            with self._lock:
                self._sending = False

    def _publish(self, position: int) -> None:
        """Put the step's command on the wire. Safe to call again to retry."""
        with self._lock:
            event = self._events[position]
            if event.status != "pending" or not event.action_id:
                return
            body = message_for(event.step, event.action_id)
        # Outside the lock: a publish can block on the socket, and a reply
        # arriving on the network thread must not wait behind it.
        self._client.publish(topic_for(self._robot), body, qos=1)

    def _current_event(self) -> StepEvent | None:
        """Caller holds the lock."""
        for event in self._events:
            if event.status == "pending" and event.action_id == self._pending:
                return event
        return None

    def _on_reply(self, _topic: str, payload: dict[str, Any]) -> None:
        """One message on the robot's topic. Runs on paho's network thread.

        Most of what arrives here is not ours: heartbeats, ready messages,
        debug traffic, and replies to whatever else is talking to this robot
        all share the topic (§1). The action_id check below is what separates
        our step's reply from all of it.
        """
        if not isinstance(payload, dict):
            return
        # §2: the firmware sends `action_id` back as either a string or a JSON
        # integer, depending on what was sent. Ours are always hex strings, so
        # compare as text rather than requiring the type to survive the trip.
        reply_id = payload.get("action_id")
        if reply_id is None:
            return

        advance: int | None = None
        with self._lock:
            if self._state != "running" or not self._pending:
                return
            if str(reply_id) != self._pending:
                return
            event = self._current_event()
            if event is None:
                return
            if not is_terminal(event.action, payload):
                # in_progress, or a progress update mid-action. Proof the robot
                # heard us, so the retry clock stops -- but not the timeout,
                # which is what catches an action that starts and never ends.
                self._retries = len(self._retry_delays)
                return

            if succeeded(event.action, payload):
                self._events[event.position] = replace(
                    event, status="complete", reply=dict(payload)
                )
                self._pending = ""
                advance = event.position + 1
                status = None
            else:
                problem = problem_from(payload)
                self._events[event.position] = replace(
                    event, status="failed", reply=dict(payload), problem=problem
                )
                self._pending = ""
                self._state = "failed"
                self._message = f"Step {event.position + 1} failed: {problem}"
                status = self._snapshot()

        if advance is None:
            self._release()
            self._notify(status)
            return
        self._advance(advance)

    def _advance(self, position: int) -> None:
        """Move to step `position`, or finish the run.

        The single place the chain moves forward, and so the place a
        generated sub-sequence would be spliced in: a completed step whose
        reply carries instructions would expand into its own events here and
        send the first of those instead of `position`. Nothing in Studio
        produces such a reply yet, which is why that branch is absent rather
        than guessed at.
        """
        with self._lock:
            if self._state != "running":
                return
            done = position >= len(self._events)
            if done:
                self._state = "complete"
                self._message = f"Finished {len(self._events)} steps."
                status = self._snapshot()

        if not done:
            self._send(position)
            return
        self._release()
        self._notify(status)

    def _release(self) -> None:
        """Stop listening. Safe to call more than once."""
        unsubscribe, self._unsubscribe = self._unsubscribe, None
        if unsubscribe is not None:
            unsubscribe()

