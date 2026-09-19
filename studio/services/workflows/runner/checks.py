"""Reading a detection verdict, so a workflow can decide whether to go again.

`run.py` owns the volley: one step in flight, correlate on action_id, stop on
failure. It deliberately knows nothing about what a detection *means* -- a
verdict there is only ever "did the detector fail", and everything else about
it is passed along untouched. This is the part that looks inside one.

The shape it reads is the Vision worker's result, as published on
`{robot}/vision` and captured from a live robot::

    {"sender":"visual_ai","status":"completed","stage":"detection_only",
     "selected_detection":{"label":"pink eraser","score":0.42,
                           "box_px":[376.1,268.7,445.3,338.1], ...},
     "detection_batch":{"detections":[...]}}

Two kinds of fact can be checked, and the difference matters:

`found` / `missing` ask only whether the detector saw the thing. Any verdict
can answer that, because it is a property of the detection itself.

`closer` / `farther` compare two *named earlier steps* by how far down the
image their object sits. That is a deliberately weak claim. The runner has no
calibration profile -- projecting a box to millimetres is the Vision worker's
job and needs the nine-point grid -- so the only depth signal available here
is the one the grid itself is built on: the object's table-contact point, and
the fact that a mono camera puts nearer things lower in the frame. The
reference point is the same one `Inverse_Kinematics_object_detect_Control.md`
specifies for the grid lookup, so a comparison made here and a projection
made there agree about which object is nearer.

It is a *comparison*, never a measurement. It can say the pink cap is farther
away than the green one; it cannot say by how many millimetres, and it must
not be used as though it could. Anything needing real distance belongs in a
`use_model` step, where the worker projects the box through the grid and
reports `ik_target.distance_mm`.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

# What a condition can assert. `found`/`missing` look at one step; `closer`/
# `farther` need a second one to compare against.
TESTS = ("found", "missing", "closer", "farther")
COMPARISONS = ("closer", "farther")

# How far up from a box's bottom edge the table-contact point is taken, as a
# fraction of the box height. The bottom of an object approximates where it
# meets the table far better than its visual centre, and the same 10% inset
# is what the Vision worker's grid lookup uses -- see `project()` and
# `reference_point()` in the worker's `calibration.py`. Keeping the two in
# step is what lets a check here predict a projection there.
CONTACT_INSET = 0.10

# A comparison between two objects at nearly the same depth is noise, not a
# fact. Detection boxes wobble by a few pixels between frames on a still
# scene, so two reference points within this many pixels are reported as
# indistinguishable rather than ordered. Measured against a live 800x600
# frame, where a 35mm real separation moved the contact point by ~130px.
DEPTH_EPSILON = 12.0


def reference_point(box_px: Sequence[float]) -> tuple[float, float]:
    """The image point where an object meets the table.

    Mirrors the Vision worker's own lookup point: horizontal centre, and
    close to the bottom edge rather than the middle.
    """
    left, top, right, bottom = (float(value) for value in box_px)
    return (left + right) / 2.0, bottom - CONTACT_INSET * (bottom - top)


def detection_of(verdict: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The one detection a verdict is about, or None if it found nothing.

    Prefers `selected_detection` -- the worker's own highest-confidence pick,
    and the one it would have acted on -- falling back to the first of the
    batch so an older result still reads.
    """
    if not isinstance(verdict, Mapping):
        return None
    selected = verdict.get("selected_detection")
    if isinstance(selected, Mapping) and selected.get("box_px"):
        return dict(selected)
    batch = verdict.get("detection_batch")
    if isinstance(batch, Mapping):
        detections = batch.get("detections")
        if isinstance(detections, Sequence):
            for item in detections:
                if isinstance(item, Mapping) and item.get("box_px"):
                    return dict(item)
    return None


def depth_of(verdict: Mapping[str, Any] | None) -> float | None:
    """How far down the frame the object sits, or None if it was not seen.

    Larger means lower in the image, which on this camera means nearer the
    robot. Only ever compared against another value from the same camera
    pose; it is not a distance.
    """
    detection = detection_of(verdict)
    if detection is None:
        return None
    try:
        return reference_point(detection["box_px"])[1]
    except (KeyError, TypeError, ValueError):
        return None


def describe(verdict: Mapping[str, Any] | None) -> str:
    """A short human reading of one verdict, for the run's status line."""
    detection = detection_of(verdict)
    if detection is None:
        return "nothing found"
    label = str(detection.get("label") or "something")
    try:
        return f"{label} at {round(float(detection['score']), 2)}"
    except (KeyError, TypeError, ValueError):
        return label


def evaluate(
    test: str,
    subject: Mapping[str, Any] | None,
    against: Mapping[str, Any] | None = None,
) -> tuple[bool, str]:
    """Whether `test` holds, and why -- in words fit for the status line.

    Never raises on a malformed or missing verdict: an unreadable result is
    a failed check, not a crashed run. A comparison against a step that saw
    nothing is unanswerable rather than true, so a loop keeps going instead
    of declaring success it cannot support.
    """
    if test not in TESTS:
        return False, f"{test!r} is not a check this runner knows."

    if test == "found":
        detection = detection_of(subject)
        return (detection is not None), (
            f"found {describe(subject)}" if detection is not None else "found nothing"
        )
    if test == "missing":
        detection = detection_of(subject)
        return (detection is None), (
            "found nothing" if detection is None else f"still sees {describe(subject)}"
        )

    here, there = depth_of(subject), depth_of(against)
    if here is None or there is None:
        absent = "the object" if here is None else "the one it compares against"
        return False, f"cannot compare: {absent} was not detected"
    gap = here - there
    if abs(gap) < DEPTH_EPSILON:
        return False, "cannot compare: both are at the same depth"
    # Lower in the frame is nearer, so a larger reference y means closer.
    nearer = gap > 0
    passed = nearer if test == "closer" else not nearer
    return passed, (
        f"it is {'closer' if nearer else 'farther'}"
        f" ({abs(gap):.0f}px apart)"
    )


__all__ = [
    "COMPARISONS",
    "CONTACT_INSET",
    "DEPTH_EPSILON",
    "TESTS",
    "depth_of",
    "describe",
    "detection_of",
    "evaluate",
    "reference_point",
]
