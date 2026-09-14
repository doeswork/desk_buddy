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
                # A robot answers on its events topic, never on the commands
                # topic it was addressed on — delivering the reply back to
                # `topic` would be a robot talking to itself.
                self.deliver(topic.replace("/commands", "/events"), reply)
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


def test_a_photo_ends_on_either_terminal_shape() -> None:
    """Two firmwares, two endings, and the runner takes whichever comes.

    §6 describes builds that send no `completed` at all and end on a second
    `in_progress` carrying log:"sent". The firmware in this repo *does* send
    `completed` — ReplyStyle::PhotoTerminal — and waiting only for the §6
    shape hung every photo step until it timed out.
    """
    assert not is_terminal("photo", {"status": "in_progress"})
    assert is_terminal("photo", {"status": "in_progress", "log": "sent"})
    assert is_terminal("photo", {"status": "completed"})
    assert is_terminal("photo", {"status": "failed"})

    # The bare in_progress carries no verdict, so it can only mean "finished".
    assert succeeded("photo", {"status": "in_progress", "log": "sent"})
    assert not succeeded("photo", {"status": "failed"})


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

    assert client.subscriptions == ["buddy/events", "buddy/vision"]
    assert client.actions == ["servo"], "the second step must not be sent yet"
    assert run.status.state == "running"

    client.deliver("buddy/events", completed(client.published[0][1]))
    assert client.actions == ["servo", "gripper"]

    client.deliver("buddy/events", completed(client.published[1][1]))
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
    client.deliver("buddy/events", {
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

    client.deliver("buddy/events", {"sender": "firmware", "status": "ready"})
    client.deliver("buddy/events", {"sender": "firmware", "status": "completed",
                                  "action_id": "someone-elses"})
    client.deliver("buddy/events", {"sender": "other", "action_id": ours})
    assert client.actions == ["servo"], "none of that was our reply"

    client.deliver("buddy/events", completed({"action_id": ours}))
    assert client.actions == ["servo", "gripper"]


def test_an_integer_action_id_still_correlates() -> None:
    """§2 says the firmware echoes the id as a string or a JSON integer
    depending on what it received. A type-strict compare would drop the reply
    and hang the step until it timed out."""
    client = FakeClient()
    run = Run(client, "buddy", [{"subject": "servo"}])
    run.start()
    reply_id = client.last_id()
    client.deliver("buddy/events", {"sender": "firmware", "status": "completed",
                                  "action_id": reply_id})
    assert run.status.state == "complete"


def test_in_progress_is_not_completion() -> None:
    client = FakeClient()
    run = Run(client, "buddy", WALK)
    run.start()
    client.deliver("buddy/events", {"sender": "firmware", "status": "in_progress",
                                  "action_id": client.last_id()})
    assert client.actions == ["servo"]
    assert run.status.state == "running"


def test_a_photo_step_advances_on_log_sent() -> None:
    client = FakeClient()
    run = Run(client, "buddy", [{"subject": "photo"}, {"subject": "gripper"}])
    run.start()
    action_id = client.last_id()

    client.deliver("buddy/events", {"sender": "firmware", "action_id": action_id,
                                  "status": "in_progress", "type": "photo"})
    assert client.actions == ["photo"], "the first in_progress is not terminal"

    client.deliver("buddy/events", {"sender": "firmware", "action_id": action_id,
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
    client.deliver("buddy/events", {"sender": "firmware", "status": "in_progress",
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
    client.deliver("buddy/events", completed({"action_id": client.last_id()}))
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
    client.deliver("buddy/events", completed(client.published[0][1]))
    assert client.actions == ["servo"]


def test_a_run_unsubscribes_when_it_finishes() -> None:
    """Otherwise every run ever started keeps a callback on the shared client."""
    client = FakeClient()
    client.autoreply = lambda payload: completed(payload)
    run = Run(client, "buddy", WALK)
    run.start()
    assert run.status.state == "complete"
    assert client._callbacks["buddy/events"] == []


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
    client.deliver("buddy/events", completed(
        client.published[0][1], base_rotation={"position": 12},
    ))
    event = run.status.events[0]
    assert isinstance(event, StepEvent)
    assert event.status == "complete"
    assert event.reply["base_rotation"] == {"position": 12}
    assert event.finished


def test_a_photo_step_ends_on_the_completed_this_firmware_sends() -> None:
    """Captured from a live robot, not invented.

    `ActionRouter` routes all four photo actions through
    `ReplyStyle::PhotoTerminal`, and `ActionController` answers that with a
    real `completed`. The runner waited only for the `in_progress` + log:"sent"
    shape from MQTT_SPEC §6, so a workflow with a detect_object step hung
    until it timed out while the robot had already finished and replied.
    """
    client = FakeClient()
    steps = [{"subject": "detect_object", "phrase": "pink eraser"}]
    run = Run(client, "buddy", steps)
    run.start()

    action_id = client.last_id()
    # The exact sequence the robot published, in order.
    client.deliver("buddy/events", {
        "sender": "firmware", "action_id": action_id, "status": "in_progress",
        "type": "detect_object", "phrase": "pink eraser",
    })
    assert run.status.state == "running", "in_progress ended the step"

    client.deliver("buddy/events", {
        "sender": "firmware", "action_id": action_id, "status": "completed",
        "type": "detect_object", "phrase": "pink eraser",
    })
    assert run.status.state == "complete", run.status.message
    assert run.status.events[0].status == "complete"


def test_a_photo_step_still_ends_on_the_legacy_sent_marker() -> None:
    """Builds described by §6 send no `completed` at all. Both shapes work,
    so one runner drives either firmware."""
    client = FakeClient()
    run = Run(client, "buddy", [{"subject": "photo"}])
    run.start()

    client.deliver("buddy/events", {
        "sender": "firmware", "action_id": client.last_id(),
        "status": "in_progress", "log": "sent",
    })
    assert run.status.state == "complete"


def test_a_failed_photo_is_a_failure_not_a_finish() -> None:
    """A photo that could not be captured answers `failed` with a structured
    error. Reading that as success would march the workflow past a step that
    produced no picture."""
    client = FakeClient()
    run = Run(client, "buddy", [{"subject": "photo"}, {"subject": "perch"}])
    run.start()

    client.deliver("buddy/events", {
        "sender": "firmware", "action_id": client.last_id(), "status": "failed",
        "error": {"code": "photo_publish_failed", "message": "No frame."},
    })

    assert run.status.state == "failed"
    assert run.status.events[0].status == "failed"
    assert run.status.events[1].status == "waiting", "a later step still ran"
    assert client.actions == ["photo"]


# ---- the detector's verdict ----------------------------------------------
# Captured from a live run: the firmware's `completed` for a detect_object
# only says it took a picture. Whether anything was *in* it comes from the
# Vision service, on a different topic, and in that run it arrived a second
# before the firmware spoke.

DETECT = {"subject": "detect_object", "phrase": "pink eraser"}


def vision_failed(action_id: str) -> dict:
    return {
        "sender": "visual_ai", "action_id": action_id, "status": "failed",
        "type": "detect_object", "stage": "detection_only",
        "error": {
            "code": "no_detection",
            "message": "No 'pink eraser' detection met the confidence threshold.",
        },
        "selected_detection": None, "robot": "buddy",
    }


def photo_completed(action_id: str) -> dict:
    return {"sender": "firmware", "action_id": action_id,
            "status": "completed", "type": "detect_object"}


def test_a_detection_that_found_nothing_stops_the_run() -> None:
    """The gap this closes: the robot took a picture, so the firmware says
    `completed`, and the workflow sailed on to grab an object that was never
    there."""
    client = FakeClient()
    run = Run(client, "buddy", [DETECT, {"subject": "gripper", "command": "GRAB"}])
    run.start()
    action_id = client.last_id()

    # The order the real run produced: detector first, firmware second.
    client.deliver("buddy/vision", vision_failed(action_id))
    client.deliver("buddy/events", photo_completed(action_id))

    status = run.status
    assert status.state == "failed", status.message
    assert "No 'pink eraser' detection" in status.events[0].problem
    assert status.events[1].status == "waiting", "the run carried on"
    assert client.actions == ["detect_object"]


def test_a_late_verdict_still_stops_the_run() -> None:
    """The detector can also speak after the firmware. The step is already
    marked complete by then, and the steps after it are already acting on a
    world that does not contain what was looked for — so it still stops."""
    client = FakeClient()
    run = Run(client, "buddy", [DETECT, {"subject": "gripper", "command": "GRAB"}])
    run.start()
    action_id = client.last_id()

    client.deliver("buddy/events", photo_completed(action_id))
    client.deliver("buddy/vision", vision_failed(action_id))

    status = run.status
    assert status.state == "failed"
    assert status.events[0].status == "failed"
    assert "No 'pink eraser' detection" in status.events[0].problem


def test_a_detection_that_found_something_carries_on() -> None:
    """A verdict is only ever a reason to stop. Success is left for the
    firmware's own terminal reply, so the step ends where every step ends."""
    client = FakeClient()
    run = Run(client, "buddy", [DETECT, {"subject": "gripper", "command": "GRAB"}])
    run.start()
    action_id = client.last_id()

    client.deliver("buddy/vision", {
        "sender": "visual_ai", "action_id": action_id, "status": "completed",
        "type": "detect_object",
        "selected_detection": {"label": "pink eraser", "score": 0.41},
    })
    client.deliver("buddy/events", photo_completed(action_id))
    client.deliver("buddy/events", completed({"action_id": client.last_id()}))

    assert run.status.state == "complete", run.status.message
    assert client.actions == ["detect_object", "gripper"]


def test_only_the_detector_may_fail_a_step_from_the_vision_topic() -> None:
    """Anything else publishing there is not the Vision service, and a step
    must not be failed by whatever else is on the broker."""
    client = FakeClient()
    run = Run(client, "buddy", [DETECT])
    run.start()
    action_id = client.last_id()

    impostor = dict(vision_failed(action_id), sender="someone_else")
    client.deliver("buddy/vision", impostor)
    assert run.status.state == "running"

    client.deliver("buddy/events", photo_completed(action_id))
    assert run.status.state == "complete"


def test_a_plain_photo_needs_no_verdict() -> None:
    """Nothing comments on a `photo` step, so waiting for one would hang it."""
    client = FakeClient()
    run = Run(client, "buddy", [{"subject": "photo"}])
    run.start()

    client.deliver("buddy/events", {
        "sender": "firmware", "action_id": client.last_id(),
        "status": "completed", "type": "photo",
    })
    assert run.status.state == "complete"


def test_commands_and_replies_use_the_two_real_topics() -> None:
    """Commands and replies are two topics, not one.

    This runner still said `{robot}/test` — a topic from before the firmware
    split them — so it published where nothing subscribes and listened where
    nothing publishes. A run would have hung on its first step forever.
    """
    from ..runner.run import reply_topic_for, topic_for

    assert topic_for("buddy") == "buddy/commands"
    assert reply_topic_for("buddy") == "buddy/events"

    client = FakeClient()
    run = Run(client, "buddy", WALK)
    run.start()

    # Events for the firmware's own replies, vision for the detector's
    # verdict on a photo — two sources, two topics.
    assert client.subscriptions == ["buddy/events", "buddy/vision"]
    assert [topic for topic, _body in client.published] == ["buddy/commands"]


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} workflow runner tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
