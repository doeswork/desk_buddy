"""Workflow runner: the send / pending / complete volley.

    python -m studio.services.workflows.tests.runner_tests

The robot is a fake client rather than a broker, so these run anywhere and
can stage the replies a real ESP32 makes awkward to produce on demand: a
failure, a photo action's second `in_progress`, silence, and the shared-topic
traffic a runner has to ignore.
"""

from __future__ import annotations

from ..runner.run import (
    Run,
    StepEvent,
    action_for,
    is_terminal,
    message_for,
    problem_from,
    succeeded,
)


class FakeClient:
    """Records publishes and lets a test reply as the firmware would."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []
        self.subscriptions: list[str] = []
        self._callbacks: dict[str, list] = {}
        # Set to a function to answer each publish the instant it happens,
        # which is what a fast robot looks like from the runner's side.
        self.autoreply = None

    def subscribe(self, topic, callback):
        self.subscriptions.append(topic)
        self._callbacks.setdefault(topic, []).append(callback)

        def unsubscribe():
            self._callbacks.get(topic, []).remove(callback)

        return unsubscribe

    def publish(self, topic, payload, *, qos=0):
        self.published.append((topic, dict(payload)))
        if self.autoreply is not None:
            reply = self.autoreply(dict(payload))
            if reply is not None:
                self.deliver(topic, reply)
        return payload.get("action_id", "")

    def deliver(self, topic, payload):
        for callback in list(self._callbacks.get(topic, ())):
            callback(topic, payload)

    @property
    def actions(self):
        return [body["action"] for _topic, body in self.published]

    def last_id(self):
        return self.published[-1][1]["action_id"]


WALK = [
    {"subject": "servo", "servoName": "ELBOW", "angle": 90},
    {"subject": "gripper", "command": "GRAB"},
]


def completed(payload, **fields):
    return {"sender": "firmware", "action_id": payload["action_id"],
            "status": "completed", **fields}


# ---- envelope ------------------------------------------------------------

def test_a_step_becomes_a_firmware_command() -> None:
    body = message_for({"subject": "servo", "servoName": "ELBOW", "angle": 90}, "abc")
    assert body == {
        "sender": "studio", "action_id": "abc", "action": "servo",
        "servoName": "ELBOW", "angle": 90,
    }


def test_custom_subjects_and_parameters_survive() -> None:
    """Workflow files are deliberately open-ended, so the runner must not be
    stricter than the format it reads."""
    body = message_for({"subject": "whatever", "nested": {"a": [1, 2]}}, "id")
    assert body["action"] == "whatever"
    assert body["nested"] == {"a": [1, 2]}


def test_a_step_cannot_forge_the_envelope() -> None:
    """A step naming its own action_id or sender would break correlation and
    could impersonate the firmware, which §2 says is ignored outright."""
    body = message_for(
        {"subject": "servo", "sender": "firmware", "action_id": "theirs",
         "action": "gripper", "status": "completed"},
        "ours",
    )
    assert body["sender"] == "studio"
    assert body["action_id"] == "ours"
    assert body["action"] == "servo"
    assert "status" not in body


def test_the_legacy_rotate_subject_still_maps() -> None:
    """Rails' one subject that was not a firmware action name. Publishing
    `rotate` verbatim would get no reply at all, per the §10 matrix."""
    assert action_for("rotate") == "baseRotate"
    assert action_for("servo") == "servo"


# ---- terminal detection --------------------------------------------------

def test_ordinary_actions_end_on_completed_or_failed() -> None:
    assert is_terminal("servo", {"status": "completed"})
    assert is_terminal("servo", {"status": "failed"})
    assert not is_terminal("servo", {"status": "in_progress"})
    assert succeeded("servo", {"status": "completed"})
    assert not succeeded("servo", {"status": "failed"})


def test_a_photo_ends_on_its_second_in_progress() -> None:
    """§6: there is no `completed` photo response. Waiting for one would hang
    the run until the timeout on every photo step."""
    assert not is_terminal("photo", {"status": "in_progress"})
    assert is_terminal("photo", {"status": "in_progress", "log": "sent"})
    assert not is_terminal("photo", {"status": "completed"})


def test_a_nested_error_is_found() -> None:
    """Detailed base and stencil failures nest the reason under the result
    key; reading only the top level reports nothing for exactly the failures
    worth reading."""
    assert problem_from({"status": "failed", "error": "flat"}) == "flat"
    assert problem_from(
        {"status": "failed", "base_rotation": {"error": "stalled"}}
    ) == "stalled"
    assert problem_from({"status": "failed"}) == "The robot reported a failure."


# ---- the volley ----------------------------------------------------------

def test_steps_run_one_at_a_time_in_order() -> None:
    """§1: the ESP32 dispatches synchronously from a single-message slot, so
    the next command may not go out until this one's reply is in."""
    client = FakeClient()
    run = Run(client, "buddy", WALK)
    run.start()

    assert client.subscriptions == ["buddy/test"]
    assert client.actions == ["servo"], "the second step must not be sent yet"
    assert run.status.state == "running"

    client.deliver("buddy/test", completed(client.published[0][1]))
    assert client.actions == ["servo", "gripper"]

    client.deliver("buddy/test", completed(client.published[1][1]))
    status = run.status
    assert status.state == "complete"
    assert [event.status for event in status.events] == ["complete", "complete"]
    assert status.position == 2


def test_a_failed_step_stops_the_run() -> None:
    """The steps after a failure operate on a world that is not where they
    think it is, so continuing is worse than stopping."""
    client = FakeClient()
    run = Run(client, "buddy", WALK)
    run.start()
    client.deliver("buddy/test", {
        "sender": "firmware", "action_id": client.last_id(),
        "status": "failed", "error": "servo stalled",
    })

    status = run.status
    assert status.state == "failed"
    assert "servo stalled" in status.message
    assert client.actions == ["servo"], "the run must not continue past a failure"
    assert status.events[0].status == "failed"
    assert status.events[1].status == "waiting"


def test_other_traffic_on_the_shared_topic_is_ignored() -> None:
    """Commands, replies, heartbeats, ready and debug messages all share
    `{robot}/test` (§1). Only a matching action_id advances the run."""
    client = FakeClient()
    run = Run(client, "buddy", WALK)
    run.start()
    ours = client.last_id()

    client.deliver("buddy/test", {"sender": "firmware", "status": "ready"})
    client.deliver("buddy/test", {"sender": "firmware", "status": "completed",
                                  "action_id": "someone-elses"})
    client.deliver("buddy/test", {"sender": "other", "action_id": ours})
    assert client.actions == ["servo"], "none of that was our reply"

    client.deliver("buddy/test", completed({"action_id": ours}))
    assert client.actions == ["servo", "gripper"]


def test_an_integer_action_id_still_correlates() -> None:
    """§2 says the firmware echoes the id as a string or a JSON integer
    depending on what it received. A type-strict compare would drop the reply
    and hang the step until it timed out."""
    client = FakeClient()
    run = Run(client, "buddy", [{"subject": "servo"}])
    run.start()
    reply_id = client.last_id()
    client.deliver("buddy/test", {"sender": "firmware", "status": "completed",
                                  "action_id": reply_id})
    assert run.status.state == "complete"


def test_in_progress_is_not_completion() -> None:
    client = FakeClient()
    run = Run(client, "buddy", WALK)
    run.start()
    client.deliver("buddy/test", {"sender": "firmware", "status": "in_progress",
                                  "action_id": client.last_id()})
    assert client.actions == ["servo"]
    assert run.status.state == "running"


def test_a_photo_step_advances_on_log_sent() -> None:
    client = FakeClient()
    run = Run(client, "buddy", [{"subject": "photo"}, {"subject": "gripper"}])
    run.start()
    action_id = client.last_id()

    client.deliver("buddy/test", {"sender": "firmware", "action_id": action_id,
                                  "status": "in_progress", "type": "photo"})
    assert client.actions == ["photo"], "the first in_progress is not terminal"

    client.deliver("buddy/test", {"sender": "firmware", "action_id": action_id,
                                  "status": "in_progress", "type": "photo",
                                  "log": "sent"})
    assert client.actions == ["photo", "gripper"]


def test_a_fast_robot_replying_inside_publish_still_advances() -> None:
    """The reply can arrive on the network thread before publish() has even
    returned, which re-enters the runner mid-send. Every step must still run
    exactly once, in order."""
    client = FakeClient()
    client.autoreply = lambda payload: completed(payload)
    run = Run(client, "buddy", WALK)
    run.start()

    status = run.status
    assert client.actions == ["servo", "gripper"]
    assert status.state == "complete"
    assert [event.status for event in status.events] == ["complete", "complete"]


# ---- retry and timeout ---------------------------------------------------

class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def test_an_unanswered_command_is_resent_then_times_out() -> None:
    """§10 lists several situations that produce no terminal reply at all.
    Without this the run would wait forever."""
    clock = Clock()
    client = FakeClient()
    run = Run(client, "buddy", WALK, timeout=60.0, retry_delays=(1.0, 5.0), now=clock)
    run.start()
    assert len(client.published) == 1

    clock.value = 1.0
    run.tick()
    assert len(client.published) == 2, "resent once"
    assert client.published[1][1]["action_id"] == client.published[0][1]["action_id"], \
        "a retry reuses the id so a duplicate is recognizable as one"

    clock.value = 5.0
    run.tick()
    assert len(client.published) == 3

    clock.value = 30.0
    run.tick()
    assert len(client.published) == 3, "retries are exhausted, not endless"
    assert run.status.state == "running"

    clock.value = 60.0
    run.tick()
    status = run.status
    assert status.state == "failed"
    assert status.events[0].status == "timed_out"
    assert "60s" in status.events[0].problem


def test_in_progress_stops_the_retries_but_not_the_timeout() -> None:
    """An acknowledged command needs no resend -- but an action that starts
    and never finishes still has to end the run rather than hang it."""
    clock = Clock()
    client = FakeClient()
    run = Run(client, "buddy", WALK, timeout=60.0, retry_delays=(1.0, 5.0), now=clock)
    run.start()
    client.deliver("buddy/test", {"sender": "firmware", "status": "in_progress",
                                  "action_id": client.last_id()})

    clock.value = 5.0
    run.tick()
    assert len(client.published) == 1, "the robot already heard us"

    clock.value = 60.0
    run.tick()
    assert run.status.state == "failed"
    assert run.status.events[0].status == "timed_out"


def test_a_completed_run_ignores_later_ticks_and_replies() -> None:
    client = FakeClient()
    client.autoreply = lambda payload: completed(payload)
    run = Run(client, "buddy", WALK)
    run.start()
    assert run.status.state == "complete"

    run.tick()
    client.deliver("buddy/test", completed({"action_id": client.last_id()}))
    assert client.actions == ["servo", "gripper"]
    assert run.status.state == "complete"


# ---- stopping and edge cases ---------------------------------------------

def test_stopping_ends_the_run_without_claiming_to_stop_the_arm() -> None:
    client = FakeClient()
    run = Run(client, "buddy", WALK)
    run.start()
    run.stop()

    status = run.status
    assert status.state == "stopped"
    assert status.events[0].status == "stopped"
    assert client.actions == ["servo"]

    # A late reply to the stopped step must not restart the chain.
    client.deliver("buddy/test", completed(client.published[0][1]))
    assert client.actions == ["servo"]


def test_a_run_unsubscribes_when_it_finishes() -> None:
    """Otherwise every run ever started keeps a callback on the shared client."""
    client = FakeClient()
    client.autoreply = lambda payload: completed(payload)
    run = Run(client, "buddy", WALK)
    run.start()
    assert run.status.state == "complete"
    assert client._callbacks["buddy/test"] == []


def test_a_run_with_no_robot_or_no_steps_says_so() -> None:
    client = FakeClient()
    assert not Run(client, "", WALK).start()
    assert not client.published

    empty = Run(client, "buddy", [])
    assert not empty.start()
    assert empty.status.state == "complete"
    assert not client.published


def test_listeners_see_each_advance() -> None:
    client = FakeClient()
    client.autoreply = lambda payload: completed(payload)
    run = Run(client, "buddy", WALK)
    seen = []
    run.on_change(lambda status: seen.append((status.state, status.position)))
    run.start()

    assert seen[-1] == ("complete", 2)
    assert ("running", 0) in seen


def test_listeners_never_see_the_run_go_backwards() -> None:
    """A reply arriving from inside publish() runs the next step -- and the
    end of the run -- before publish() returns. Notifying after the publish
    delivered those states in reverse, so a listener saw "complete" and then
    "running", and a UI would render a finished run as still going."""
    client = FakeClient()
    client.autoreply = lambda payload: completed(payload)
    run = Run(client, "buddy", WALK)
    seen = []
    run.on_change(lambda status: seen.append(status.position))
    run.start()

    assert seen == sorted(seen), f"states arrived out of order: {seen}"
    assert seen[-1] == 2


def test_a_long_workflow_does_not_exhaust_the_stack() -> None:
    """A robot on loopback replies from inside publish(), so the send chain
    re-enters itself once per step. Recursing there died partway through a
    routine of a few hundred steps -- a plausible length, not a pathological
    one."""
    client = FakeClient()
    client.autoreply = lambda payload: completed(payload)
    steps = [{"subject": "servo", "index": index} for index in range(1000)]
    run = Run(client, "buddy", steps)
    run.start()

    status = run.status
    assert status.state == "complete"
    assert status.position == 1000
    assert [body["index"] for _topic, body in client.published] == list(range(1000)), \
        "every step ran exactly once, in order"


def test_one_bad_listener_cannot_halt_a_run() -> None:
    client = FakeClient()
    client.autoreply = lambda payload: completed(payload)
    run = Run(client, "buddy", WALK)
    run.on_change(lambda status: 1 / 0)
    seen = []
    run.on_change(lambda status: seen.append(status.state))
    run.start()

    assert run.status.state == "complete"
    assert seen, "the second listener still ran"


def test_a_step_event_reports_what_came_back() -> None:
    client = FakeClient()
    run = Run(client, "buddy", [{"subject": "baseRotate", "controlType": "status"}])
    run.start()
    client.deliver("buddy/test", completed(
        client.published[0][1], base_rotation={"position": 12},
    ))
    event = run.status.events[0]
    assert isinstance(event, StepEvent)
    assert event.status == "complete"
    assert event.reply["base_rotation"] == {"position": 12}
    assert event.finished


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} workflow runner tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
