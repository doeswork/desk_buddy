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


# ---- verify, check, and repeat_until -------------------------------------
#
# The boxes below are real, captured from robot-1 on 2026-09-19 at 800x600.
# The pink cap sat at 37.5mm and the green at 72.5mm by the nine-point grid,
# and the pink box really is lower in the frame — which is the whole basis of
# the closer/farther comparison, so the fixture keeps the true numbers.
PINK_NEAR = [376.1, 268.7, 445.3, 338.1]     # 37.5mm: nearer, lower in frame
PINK_FAR = [352.6, 1.2, 410.4, 130.8]        # 92.5mm: farther, higher up
GREEN = [390.0, 139.3, 437.0, 175.1]         # 72.5mm, unmoved throughout


def seen(payload, box, label="thing"):
    """A detector verdict for one detection, as published on {robot}/vision."""
    return {
        "sender": "visual_ai", "action_id": payload["action_id"],
        "status": "completed", "type": "detect_object",
        "stage": "detection_only",
        "selected_detection": {"label": label, "score": 0.42, "box_px": list(box)},
        "detection_batch": {"image_width": 800, "image_height": 600,
                            "prompt": label, "detections": [
                                {"label": label, "score": 0.42, "box_px": list(box)}]},
    }


def nothing(payload, label="thing"):
    """The verdict when the detector found nothing: a failure, on this topic."""
    return {
        "sender": "visual_ai", "action_id": payload["action_id"],
        "status": "failed", "type": "detect_object", "stage": "detection_only",
        "error": {"code": "no_detection", "message": f"No {label!r} detection."},
        "detection_batch": {"image_width": 800, "image_height": 600,
                            "prompt": label, "detections": []},
    }


def looking(*boxes):
    """Answer each verify_object with the next staged box, then complete it."""
    staged = list(boxes)

    def reply(client, payload):
        if payload["action"] == "detect_object":
            box = staged.pop(0) if staged else None
            verdict = seen(payload, box) if box is not None else nothing(payload)
            client.deliver("buddy/vision", verdict)
        return completed(payload)

    return reply


def stage(client, *boxes):
    """Wire `looking` into a FakeClient's autoreply."""
    reply = looking(*boxes)
    client.autoreply = lambda payload: reply(client, payload)
    return client


def test_the_three_detection_subjects_differ_only_in_use_model() -> None:
    """One firmware action, three jobs, and the flag set by the subject.

    `use_model` decides whether the Vision worker moves the arm after it
    projects the box. Taking it from the subject rather than the step is what
    stops a verify_object from ever moving, however the step was written.
    """
    look = message_for({"subject": "detect_object", "phrase": "cup"}, "a")
    grab = message_for({"subject": "reach_and_grab", "phrase": "cup"}, "b")
    verify = message_for({"subject": "verify_object", "phrase": "cup",
                          "name": "the cup"}, "c")

    assert look["action"] == grab["action"] == verify["action"] == "detect_object"
    assert look["use_model"] is False
    assert grab["use_model"] is True
    assert verify["use_model"] is False
    # `name` is Studio's label for the observation; the robot never sees it.
    assert "name" not in verify


def test_a_step_cannot_make_a_verify_move_the_arm() -> None:
    """The subject wins over anything the step says about use_model."""
    body = message_for(
        {"subject": "verify_object", "phrase": "cup", "use_model": True}, "a"
    )
    assert body["use_model"] is False


def test_a_verify_that_finds_nothing_does_not_fail_the_run() -> None:
    """Finding nothing answers the question; it does not end the workflow.

    Every other detection subject treats an empty result as a failure,
    because the steps after it were about to act on something absent. A
    verify is a question, and "nothing there" is one of its answers — the
    one a `missing` check is waiting for.
    """
    client = stage(FakeClient(), None)
    run = Run(client, "buddy", [
        {"subject": "verify_object", "phrase": "cup", "name": "cup"},
        {"subject": "perch"},
    ])
    run.start()

    status = run.status
    assert status.state == "complete", status.message
    assert client.actions == ["detect_object", "perch"]


def test_a_check_reads_the_observation_a_verify_named() -> None:
    client = stage(FakeClient(), PINK_NEAR)
    run = Run(client, "buddy", [
        {"subject": "verify_object", "phrase": "pink eraser", "name": "pink"},
        {"subject": "check", "check": "found", "of": "pink"},
        {"subject": "perch"},
    ])
    run.start()

    status = run.status
    assert status.state == "complete", status.message
    # The check never reached the robot: two commands, not three.
    assert client.actions == ["detect_object", "perch"]
    assert "found" in status.events[1].note


def test_a_failed_check_stops_the_run() -> None:
    """A check is an assertion. The steps after it assume it held."""
    client = stage(FakeClient(), None)
    run = Run(client, "buddy", [
        {"subject": "verify_object", "phrase": "pink eraser", "name": "pink"},
        {"subject": "check", "check": "found", "of": "pink"},
        {"subject": "perch"},
    ])
    run.start()

    status = run.status
    assert status.state == "failed", status.message
    assert status.events[1].status == "failed"
    # `perch` never ran: the run stopped at the failed assertion.
    assert client.actions == ["detect_object"]


def test_closer_and_farther_compare_two_observations() -> None:
    """The real captured geometry: pink near, green behind it."""
    client = stage(FakeClient(), PINK_NEAR, GREEN)
    run = Run(client, "buddy", [
        {"subject": "verify_object", "phrase": "pink eraser", "name": "pink"},
        {"subject": "verify_object", "phrase": "green eraser", "name": "green"},
        {"subject": "check", "check": "closer", "of": "pink", "against": "green"},
    ])
    run.start()
    assert run.status.state == "complete", run.status.message

    # And the same pair, the other way round, is false.
    client = stage(FakeClient(), PINK_NEAR, GREEN)
    run = Run(client, "buddy", [
        {"subject": "verify_object", "phrase": "pink eraser", "name": "pink"},
        {"subject": "verify_object", "phrase": "green eraser", "name": "green"},
        {"subject": "check", "check": "farther", "of": "pink", "against": "green"},
    ])
    run.start()
    assert run.status.state == "failed"


def test_a_comparison_against_something_unseen_is_not_a_pass() -> None:
    """Unanswerable is not true. A loop must keep going, not declare success."""
    client = stage(FakeClient(), PINK_NEAR, None)
    run = Run(client, "buddy", [
        {"subject": "verify_object", "phrase": "pink eraser", "name": "pink"},
        {"subject": "verify_object", "phrase": "green eraser", "name": "green"},
        {"subject": "check", "check": "farther", "of": "pink", "against": "green"},
    ])
    run.start()

    status = run.status
    assert status.state == "failed"
    assert "cannot compare" in status.events[2].note


def test_repeat_until_goes_back_and_passes_on_the_second_look() -> None:
    """The loop the pink-eraser routine needs.

    First look: the cap is still nearer than the green one, so the condition
    does not hold and the run goes back to the named step. Second look —
    after the retried work — it is farther, and the run carries on.
    """
    client = stage(FakeClient(), PINK_NEAR, GREEN, PINK_FAR, GREEN)
    run = Run(client, "buddy", [
        {"subject": "perch", "name": "start"},
        {"subject": "verify_object", "phrase": "pink eraser", "name": "pink"},
        {"subject": "verify_object", "phrase": "green eraser", "name": "green"},
        {"subject": "repeat_until", "until": "farther", "of": "pink",
         "against": "green", "then": "start", "max_attempts": 4},
        {"subject": "gripper", "command": "DROP"},
    ])
    run.start()

    status = run.status
    assert status.state == "complete", status.message
    # Round one, round two, then the step after the loop.
    assert client.actions == [
        "perch", "detect_object", "detect_object",
        "perch", "detect_object", "detect_object",
        "gripper",
    ]
    assert status.events[3].attempts == 2


def test_repeat_until_gives_up_rather_than_looping_forever() -> None:
    """A condition the robot can never satisfy still has to end the run."""
    client = stage(FakeClient(), *([PINK_NEAR, GREEN] * 10))
    run = Run(client, "buddy", [
        {"subject": "perch", "name": "start"},
        {"subject": "verify_object", "phrase": "pink eraser", "name": "pink"},
        {"subject": "verify_object", "phrase": "green eraser", "name": "green"},
        {"subject": "repeat_until", "until": "farther", "of": "pink",
         "against": "green", "then": "start", "max_attempts": 3},
        {"subject": "gripper", "command": "DROP"},
    ])
    run.start()

    status = run.status
    assert status.state == "failed", status.message
    assert status.events[3].attempts == 3
    assert "gave up" in status.events[3].problem
    # It stopped at the repeat: the step after the loop never ran.
    assert "gripper" not in client.actions
    assert client.actions.count("perch") == 3


def test_a_repeat_may_not_ask_for_more_attempts_than_the_ceiling() -> None:
    """`max_attempts` may lower the bound; it may not raise it."""
    from ..runner.run import MAX_ATTEMPTS, Run as _Run

    limit = _Run._attempt_limit
    assert limit({"max_attempts": 3}) == 3
    assert limit({"max_attempts": 9999}) == MAX_ATTEMPTS
    assert limit({}) == MAX_ATTEMPTS
    assert limit({"max_attempts": "lots"}) == MAX_ATTEMPTS
    assert limit({"max_attempts": 0}) == 1


def test_a_repeat_that_names_no_step_to_return_to_fails_clearly() -> None:
    """Better a named failure than a loop that quietly runs nothing again."""
    client = stage(FakeClient(), PINK_NEAR, GREEN)
    run = Run(client, "buddy", [
        {"subject": "verify_object", "phrase": "pink eraser", "name": "pink"},
        {"subject": "verify_object", "phrase": "green eraser", "name": "green"},
        {"subject": "repeat_until", "until": "farther", "of": "pink",
         "against": "green", "then": "nowhere"},
    ])
    run.start()

    status = run.status
    assert status.state == "failed"
    assert "does not name a step" in status.events[2].problem


def test_a_retried_block_does_not_keep_the_first_attempts_results() -> None:
    """A view must not show round one's answer beside round two's."""
    client = stage(FakeClient(), PINK_NEAR, GREEN, PINK_FAR, GREEN)
    seen_states = []
    run = Run(client, "buddy", [
        {"subject": "perch", "name": "start"},
        {"subject": "verify_object", "phrase": "pink eraser", "name": "pink"},
        {"subject": "verify_object", "phrase": "green eraser", "name": "green"},
        {"subject": "repeat_until", "until": "farther", "of": "pink",
         "against": "green", "then": "start"},
    ])
    run.on_change(lambda status: seen_states.append(
        [event.status for event in status.events]))
    run.start()

    assert run.status.state == "complete", run.status.message
    # Somewhere in the middle the loop put the block back to waiting.
    assert any(states[0] == "waiting" for states in seen_states[1:])


def test_a_verify_waits_for_the_detector_not_the_firmware() -> None:
    """The ordering that broke a live run, and the reason verify waits.

    Captured from robot-1 on 2026-09-19: the firmware answered `completed` at
    :33.967 and the detector published its result at :34.357 — 390ms later.
    The runner advanced on the firmware's word, sent the next detection 5ms
    after it, and got `detector_busy`; the first verdict then arrived after
    its own step had ended and was recorded against nothing.

    So a verify_object holds until the verdict lands. Here the firmware
    replies immediately and the detector only speaks when `release` is
    called, which is that timing with the delay made explicit.
    """
    client = FakeClient()
    pending = []
    client.autoreply = lambda payload: (
        pending.append(payload) or completed(payload)
        if payload["action"] == "detect_object" else completed(payload)
    )
    run = Run(client, "buddy", [
        {"subject": "verify_object", "phrase": "pink eraser", "name": "pink"},
        {"subject": "perch"},
    ])
    run.start()

    # The firmware has finished, but the detector has not spoken. The run
    # must still be on step 1 — nothing may be sent into a busy detector.
    assert run.status.events[0].status == "pending"
    assert client.actions == ["detect_object"]

    client.deliver("buddy/vision", seen(pending[0], PINK_NEAR, "pink eraser"))

    status = run.status
    assert status.state == "complete", status.message
    assert status.events[0].status == "complete"
    assert client.actions == ["detect_object", "perch"]


def test_a_verify_still_ends_when_the_detector_speaks_first() -> None:
    """The other order, which is what a fast detector actually does."""
    client = stage(FakeClient(), PINK_NEAR)
    run = Run(client, "buddy", [
        {"subject": "verify_object", "phrase": "pink eraser", "name": "pink"},
        {"subject": "check", "check": "found", "of": "pink"},
    ])
    run.start()
    assert run.status.state == "complete", run.status.message


def test_a_verify_that_never_hears_the_detector_times_out() -> None:
    """Holding for a verdict must not mean holding forever."""
    clock = Clock()
    client = FakeClient()
    client.autoreply = lambda payload: completed(payload)
    run = Run(client, "buddy", [
        {"subject": "verify_object", "phrase": "pink eraser", "name": "pink"},
    ], now=clock, timeout=30.0)
    run.start()

    assert run.status.events[0].status == "pending"
    clock.value += 31.0
    run.tick()

    status = run.status
    assert status.state == "failed", status.message
    assert status.events[0].status == "timed_out"


def test_a_reach_and_grab_waits_for_the_worker_not_the_photo() -> None:
    """The bug that closed the gripper in mid-air.

    Captured from robot-1 on 2026-09-19. `reach_and_grab` makes the Vision
    worker drive baseRotate, controlik and GRAB itself, one command at a
    time -- a sequence that ran for 30 seconds. But the firmware answers the
    *photo* almost at once, and the runner took that as the step finishing:
    it advanced 15 seconds early and published the next step's `perch` into
    the middle of the worker's motion. Two conversations drove one arm, the
    perch lifted it off the table mid-reach, and the GRAB closed on air.

    The firmware's `completed` cannot end this step. Only the worker's own
    terminal result can, because only it means the whole sequence is over.
    """
    client = FakeClient()
    grabs = []
    client.autoreply = lambda payload: (
        grabs.append(payload) or completed(payload)
        if payload["action"] == "detect_object" else completed(payload)
    )
    run = Run(client, "buddy", [
        {"subject": "reach_and_grab", "phrase": "pink eraser"},
        {"subject": "perch"},
    ])
    run.start()

    # The step perches first, then looks: two commands, one step.
    assert client.actions == ["perch", "detect_object"]
    # The photo is answered, but the worker is still moving the arm. Nothing
    # may be published until it says it has finished.
    assert run.status.events[0].status == "pending"

    client.deliver("buddy/vision", {
        "sender": "visual_ai", "action_id": grabs[0]["action_id"],
        "status": "completed", "type": "detect_object",
        "stage": "reach_and_grab_completed", "grab_status": "completed",
    })

    status = run.status
    assert status.state == "complete", status.message
    assert client.actions == ["perch", "detect_object", "perch"]


def test_a_failed_reach_and_grab_stops_the_run() -> None:
    """A grab that failed may have left the arm somewhere unexpected."""
    client = FakeClient()
    grabs = []
    client.autoreply = lambda payload: (
        grabs.append(payload) or completed(payload)
        if payload["action"] == "detect_object" else completed(payload)
    )
    run = Run(client, "buddy", [
        {"subject": "reach_and_grab", "phrase": "pink eraser"},
        {"subject": "perch"},
    ])
    run.start()
    client.deliver("buddy/vision", {
        "sender": "visual_ai", "action_id": grabs[0]["action_id"],
        "status": "failed", "type": "detect_object",
        "stage": "reach_and_grab_failed",
        "error": {"code": "robot_command_failed",
                  "message": "Firmware reported that gripper failed."},
    })

    status = run.status
    assert status.state == "failed", status.message
    assert status.events[0].status == "failed"
    assert "gripper failed" in status.events[0].problem
    # The step after it never ran, and with no retries budget it did not
    # try again either.
    assert client.actions == ["perch", "detect_object"]


def test_the_workers_progress_reports_do_not_end_a_held_step() -> None:
    """The second half of the mid-air grab, and a subtler bug than the first.

    The Vision worker narrates a reach_and_grab while it runs it:
    target_planned, executing_base, executing_ik, executing_grab -- all
    `in_progress`, all arriving on the vision topic while the arm is moving.
    The runner filed each one as the step's verdict, so by the time the
    firmware answered the photo the step looked answered too, the hold did
    not engage, and the next command went out mid-motion anyway.

    Only `completed` and `failed` are verdicts. Everything else is the
    worker thinking out loud.
    """
    client = FakeClient()
    grabs = []
    client.autoreply = lambda payload: (
        grabs.append(payload) or completed(payload)
        if payload["action"] == "detect_object" else completed(payload)
    )
    run = Run(client, "buddy", [
        {"subject": "reach_and_grab", "phrase": "pink eraser"},
        {"subject": "controlik", "distance": 100.0, "z_height": 0.0},
    ])
    run.start()

    for stage in ("target_planned", "executing_base", "executing_ik",
                  "executing_grab"):
        client.deliver("buddy/vision", {
            "sender": "visual_ai", "action_id": grabs[0]["action_id"],
            "status": "in_progress", "type": "detect_object", "stage": stage,
        })
        # Still holding, every time: the arm is mid-sequence and nothing
        # more may be published into it.
        assert run.status.events[0].status == "pending", stage
        assert client.actions == ["perch", "detect_object"], stage

    client.deliver("buddy/vision", {
        "sender": "visual_ai", "action_id": grabs[0]["action_id"],
        "status": "completed", "type": "detect_object",
        "stage": "reach_and_grab_completed",
    })

    status = run.status
    assert status.state == "complete", status.message
    assert client.actions == ["perch", "detect_object", "controlik"]


# ---- retrying a missed grab ---------------------------------------------

def grabbing(*outcomes):
    """Answer each reach_and_grab with the next staged outcome.

    Each outcome is True for a completed grab or a message for a failed one.
    The firmware's own replies are always `completed`: it took the photo
    either way, and it is the Vision worker's verdict that says whether the
    grab worked.
    """
    staged = list(outcomes)

    def reply(client, payload):
        if payload["action"] == "detect_object":
            outcome = staged.pop(0) if staged else True
            done = completed(payload)
            client.deliver("buddy/vision", {
                "sender": "visual_ai", "action_id": payload["action_id"],
                "status": "completed" if outcome is True else "failed",
                "type": "detect_object",
                "stage": ("reach_and_grab_completed" if outcome is True
                          else "reach_and_grab_failed"),
                **({} if outcome is True
                   else {"error": {"code": "robot_command_failed",
                                   "message": str(outcome)}}),
            })
            return done
        return completed(payload)

    return reply


def test_a_reach_and_grab_perches_before_it_looks() -> None:
    """The camera pose is part of the step, not the workflow's job.

    The nine-point grid maps *this* pose to the table, so a photo from any
    other one projects to the wrong place -- and the Vision worker rejects
    the attempt when the pose does not match the profile. Making the perch
    part of the step is also what lets a retry mean "look again properly"
    rather than "look again from wherever the missed grab left the arm".
    """
    client = FakeClient()
    client.autoreply = lambda payload: grabbing(True)(client, payload)
    run = Run(client, "buddy", [{"subject": "reach_and_grab", "phrase": "cup"}])
    run.start()

    assert run.status.state == "complete", run.status.message
    assert client.actions == ["perch", "detect_object"]
    # Two commands, but still one step on the page.
    assert run.status.total == 1


def test_a_missed_grab_is_retried_from_the_perch() -> None:
    """The point of `retries`: try again rather than stopping the routine."""
    client = FakeClient()
    reply = grabbing("Firmware reported that gripper failed.", True)
    client.autoreply = lambda payload: reply(client, payload)
    run = Run(client, "buddy", [
        {"subject": "reach_and_grab", "phrase": "pink eraser", "retries": 2},
        {"subject": "gripper", "command": "DROP"},
    ])
    run.start()

    status = run.status
    assert status.state == "complete", status.message
    # Missed once, perched and looked again, then carried on.
    assert client.actions == [
        "perch", "detect_object", "perch", "detect_object", "gripper",
    ]
    assert status.events[0].retried == 1
    assert status.events[0].status == "complete"


def test_retries_run_out_and_the_run_then_fails() -> None:
    """A budget, not a promise. A grab that never works still ends the run."""
    client = FakeClient()
    reply = grabbing(*(["gripper failed"] * 6))
    client.autoreply = lambda payload: reply(client, payload)
    run = Run(client, "buddy", [
        {"subject": "reach_and_grab", "phrase": "pink eraser", "retries": 2},
        {"subject": "gripper", "command": "DROP"},
    ])
    run.start()

    status = run.status
    assert status.state == "failed", status.message
    assert status.events[0].retried == 2
    # One first go plus two retries, and the step after it never ran.
    assert client.actions.count("detect_object") == 3
    assert "gripper" not in client.actions


def test_no_retries_means_one_attempt() -> None:
    """Zero is a real answer: try once, and let a person decide."""
    client = FakeClient()
    reply = grabbing("gripper failed")
    client.autoreply = lambda payload: reply(client, payload)
    run = Run(client, "buddy", [
        {"subject": "reach_and_grab", "phrase": "cup", "retries": 0},
    ])
    run.start()

    assert run.status.state == "failed"
    assert client.actions.count("detect_object") == 1


def test_retries_are_clamped_to_the_allowed_range() -> None:
    """0 to 10, whatever the file says."""
    from ..runner.run import MAX_RETRIES, MIN_RETRIES, Run as _Run

    limit = _Run._retry_limit
    assert limit({"retries": 0}) == MIN_RETRIES == 0
    assert limit({"retries": 3}) == 3
    assert limit({"retries": 10}) == MAX_RETRIES == 10
    assert limit({"retries": 99}) == MAX_RETRIES
    assert limit({"retries": -5}) == MIN_RETRIES
    assert limit({"retries": "lots"}) == MIN_RETRIES
    # Absent means no retries, so an existing workflow behaves as before.
    assert limit({}) == MIN_RETRIES


def test_the_robot_never_sees_the_retries_field() -> None:
    """`retries` is Studio's bookkeeping; the firmware has no idea."""
    body = message_for(
        {"subject": "reach_and_grab", "phrase": "cup", "retries": 4}, "a"
    )
    assert "retries" not in body
    assert body["use_model"] is True


def test_a_perch_that_fails_does_not_go_on_to_look() -> None:
    """Looking from an unknown pose would project to the wrong place."""
    client = FakeClient()
    client.autoreply = lambda payload: (
        {"sender": "firmware", "action_id": payload["action_id"],
         "status": "failed", "error": "servo stalled"}
        if payload["action"] == "perch" else completed(payload)
    )
    run = Run(client, "buddy", [
        {"subject": "reach_and_grab", "phrase": "cup", "retries": 1},
    ])
    run.start()

    status = run.status
    assert status.state == "failed", status.message
    assert "could not perch" in status.events[0].problem
    assert "detect_object" not in client.actions


def test_a_retry_keeps_the_loop_and_the_retry_budget_apart() -> None:
    """`repeat_until` counts blocks; `retries` counts grabs within one step.

    Both are bounded, and neither spends the other's budget: here the grab
    misses once inside the first pass round the loop, retries, succeeds, and
    the loop still gets its own full count of attempts.
    """
    client = FakeClient()
    verdicts = ["gripper failed", True]
    looks = [PINK_NEAR, GREEN, PINK_FAR, GREEN]

    def reply(payload):
        if payload["action"] != "detect_object":
            return completed(payload)
        if payload.get("use_model"):
            outcome = verdicts.pop(0) if verdicts else True
            client.deliver("buddy/vision", {
                "sender": "visual_ai", "action_id": payload["action_id"],
                "status": "completed" if outcome is True else "failed",
                "type": "detect_object",
                "stage": ("reach_and_grab_completed" if outcome is True
                          else "reach_and_grab_failed"),
                **({} if outcome is True
                   else {"error": {"message": str(outcome)}}),
            })
        else:
            box = looks.pop(0) if looks else PINK_FAR
            client.deliver("buddy/vision", seen(payload, box))
        return completed(payload)

    client.autoreply = reply
    run = Run(client, "buddy", [
        {"subject": "perch", "name": "start"},
        {"subject": "reach_and_grab", "phrase": "pink eraser", "retries": 2},
        {"subject": "verify_object", "phrase": "pink eraser", "name": "pink"},
        {"subject": "verify_object", "phrase": "green eraser", "name": "green"},
        {"subject": "repeat_until", "until": "farther", "of": "pink",
         "against": "green", "then": "start", "max_attempts": 3},
    ])
    run.start()

    status = run.status
    assert status.state == "complete", status.message
    # The grab spent one retry; the loop went round twice.
    assert status.events[1].retried == 1
    assert status.events[4].attempts == 2


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} workflow runner tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
