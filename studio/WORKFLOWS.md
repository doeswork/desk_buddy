# Writing a Desk Buddy workflow

A workflow is one JSON document: some metadata, and an ordered array of steps.
One file per workflow, so they can be copied, diffed and hand-edited without
dragging every other workflow along.

```text
Linux   ~/.local/share/DeskBuddy/Studio/workflows/<name>.json
```

The file's name and the `workflow.name` inside it must match, and the name has
to be 1–64 lowercase letters, numbers, underscores or hyphens.

```json
{
  "workflow": {
    "name": "pick_up_the_cup",
    "description": "What this routine does, and anything the next person needs to know.",
    "validation": true
  },
  "steps": [
    {"subject": "reach_and_grab", "phrase": "red cup", "retries": 2}
  ]
}
```

Every step needs a `subject`. Everything else in the step is passed to the
robot untouched, so a custom subject with custom fields still round-trips —
workflows are allowed to be special. `firmware/README.md` is the contract for
what the robot accepts; this file covers the parts Studio adds on top.

## Subjects that are firmware actions

Most subjects are published straight through as the firmware action of the
same name: `perch`, `servo`, `controlik`, `gripper`, `baseRotate`, `photo`,
`detect_color`, `calibrate`, `calibrationvalues`, `stencilCalibrate`,
`ota_update`. Their fields are the firmware's fields, documented in
`firmware/README.md`. The legacy subject `rotate` is still accepted as an
alias for `baseRotate`.

## The three detection subjects

All three publish the firmware action `detect_object`, because that is the
only thing the robot and the Vision worker understand. They are separate
subjects because they are three different jobs, and a workflow that says which
one it means reads far better than one subject plus a boolean:

| Subject | What it does | Moves the arm? |
|---|---|---|
| `detect_object` | A plain look. Nothing depends on the result. | no |
| `verify_object` | A look whose result a later check reads. | no |
| `reach_and_grab` | Perch, look, then pick the object up. | **yes** |

`use_model` is set from the subject, not from the step — so a `verify_object`
cannot move the arm however its step is written, and a `reach_and_grab` cannot
quietly fail to. Do not write `use_model` yourself; it is ignored.

`phrase` must be a **scalar string**. A list is rejected by the Vision worker
before any photo is taken.

### `reach_and_grab`

```json
{"subject": "reach_and_grab", "phrase": "pink object", "retries": 3}
```

This one step publishes two commands: a `perch`, then the detection. The perch
is part of the step rather than the workflow's job, because the nine-point grid
maps *that specific camera pose* to the table — a photo from anywhere else
projects to the wrong place, and the Vision worker rejects the attempt outright
when the pose does not match the profile it calibrated. If the perch fails the
step fails without looking, because looking from an unknown pose is meaningless.

After the photo, the Vision worker drives `baseRotate` → `controlik` →
`gripper GRAB` itself. **Do not add those steps after it** — they are already
done, and a command published while the worker is mid-sequence fights it for
the arm. The step does not finish until the worker says the whole sequence is
over.

`retries` is `0`–`10`, default `0`. A missed grab re-runs the whole step,
perch included, so each retry looks again from the calibrated pose rather than
from wherever the failed attempt left the arm. A grab that closes on nothing is
a normal `failed` from firmware, not a fault — see the gripper section of
`firmware/README.md`.

### `verify_object`

```json
{"subject": "verify_object", "phrase": "pink object", "name": "pink"}
```

Never moves, and its `name` is how a later check refers to what it saw. The
name is Studio's own bookkeeping and is never sent to the robot. Looking again
under the same name replaces the earlier observation, which is what makes a
condition re-read correctly each time round a loop.

Unlike the other two, a `verify_object` that finds nothing does **not** fail
the run. "Nothing there" is a legitimate answer to a question — it is the
answer a `missing` check is waiting for — so the verdict is recorded and the
check decides what it means.

`verify_object` does not perch. Put a `perch` before it if the arm might be
out over the table blocking the camera; a `controlik` followed by a `DROP`
leaves it exactly there.

## Checks and loops

Two subjects are answered by Studio itself and never reach the robot.

### `check` — assert, or stop

```json
{"subject": "check", "check": "farther", "of": "pink", "against": "green"}
```

If the condition holds the run carries on; if not, the run fails there. Use it
when the steps afterwards would be acting on a world that is not as they assume.

### `repeat_until` — go back and try again

```json
{"subject": "repeat_until", "until": "farther", "of": "pink",
 "against": "green", "then": "start here", "max_attempts": 3}
```

Same conditions, but a failure sends the run *back* to the step named by
`then` and runs the block again. `then` matches an earlier step's `name` (or a
1-based step number) and must come before the repeat. `max_attempts` is `1`–`10`,
default `10`; when it runs out the run fails rather than looping forever.

Steps between the target and the repeat are reset to waiting on each pass, so a
view never shows one attempt's result beside the next one's.

### Conditions

| `check` / `until` | True when |
|---|---|
| `found` | the named observation saw something |
| `missing` | it saw nothing |
| `closer` | `of` is nearer the robot than `against` |
| `farther` | `of` is further from the robot than `against` |

`of` and `against` name `verify_object` steps. `closer`/`farther` compare how
far down the frame each object's table-contact point sits, using the same 10%
inset the calibration grid uses — so a comparison here agrees with the grid
about which object is nearer.

**It is a comparison, not a measurement.** It can say the pink cap is farther
away than the green one; it cannot say by how many millimetres. Two objects
within ~12px of the same depth are reported as indistinguishable rather than
ordered, and a comparison against something that was not detected is
unanswerable — both are treated as "not yet", so a loop keeps going rather than
declaring a success it cannot support. Anything needing real millimetres
belongs in a `reach_and_grab`, where the worker projects through the grid.

## A complete example

Move the pink cap beyond the green one, and keep going until it is:

```json
{
  "workflow": {
    "name": "pink_eraser_futher_than_green",
    "description": "…",
    "validation": true
  },
  "steps": [
    {"subject": "reach_and_grab", "phrase": "pink object",
     "retries": 3, "name": "start here"},
    {"subject": "controlik", "distance": 100.0, "z_height": 0.0},
    {"subject": "gripper", "command": "DROP"},
    {"subject": "perch"},
    {"subject": "verify_object", "phrase": "pink object", "name": "pink"},
    {"subject": "verify_object", "phrase": "green object", "name": "green"},
    {"subject": "repeat_until", "until": "farther", "of": "pink",
     "against": "green", "then": "start here", "max_attempts": 3}
  ]
}
```

The pick is closed-loop and needs no hand-tuning. The place is open-loop —
`distance: 100.0` is a number somebody chose by reading where the green cap
was — and the `repeat_until` is what turns that guess into something that
corrects itself.

## Things that will bite you

**Detection is probabilistic, and phrasing matters a lot.** The same unmoved
scene can answer `no_detection` on one frame and detect confidently on the
next. Measured on robot-1: `"pink eraser"` and `"green eraser"` returned no
detection at all, while `"pink object"` and `"green object"` detected 6 times
out of 6 on the identical scene. If a phrase keeps missing, try a more generic
noun before assuming the object cannot be seen.

**`z_height` must be 0 unless the z=50 calibration exists.** Check
`ik_z50_calibrated` in a `calibrationvalues` reply. Without it every nonzero
`z_height` is rejected, so a routine cannot lift one object over another — it
slides along the table, and the drop point has to be chosen so the held object
does not shove the other one out of the way.

**`baseRotate` with `controlType: "ANGLE"` needs `0 <= value < 360`.** The
calibration grid emits `-30°`–`+30°`, so a target of `-20` is sent as `340`.
Negatives are rejected outright.

**A vision-driven step needs an active calibration profile.** If Studio has no
"active" profile for the robot, every `reach_and_grab` fails immediately with
`missing_calibration_profile`, however good the detection is. The profile's
perch must also match the robot's saved perch, or it fails with
`calibration_pose_mismatch`.

**A run that stops does not stop the arm.** There is no firmware cancel for an
arbitrary action; stopping a run stops Studio listening and advancing. The
robot finishes whatever it was already doing.

**Check the gripper before blaming the workflow.** `GRAB` replies `failed`
whenever it closes on nothing and reopens the jaws — that is the design. If a
routine keeps missing, read the gripper `debug` lines on the event topic to see
whether the touch sensors ever tripped.
