# Vision → inverse-kinematics plan

## Repository findings

The current branch is `restore-9-point-calibrate` at `73f078d`. Its active
vision path is the small MQTT detector under
`studio/services/vision/worker_runtime/desk_buddy_vision_worker/`.

The historical code is split across three branches:

| Branch | Useful contents | Decision |
| --- | --- | --- |
| `studio-basic-vision` (`3e12b51`, remote tip `0d2111c`) | Current-style isolated zero-shot detector, model catalog, robot photo routing, and preview UI | Keep the current MQTT path; it has no nine-point implementation. |
| `bigger-rewrite-vision` (`59673a4`; nine-point code introduced by `68c4598`) | `studio/services/vision/ik_control/calibration.py`, `calibration_photo.py`, deterministic grid projection, IK target validation, and command sequencing | Port the pure calibration and safety ideas from here. |
| `vision_services` (`7aa50d7`) | Older complete MQTT coordinator with calibration, depth, artifacts, planning, and tests | Use as protocol/design reference only; its coordinator, topics, storage, and `/test` robot path do not match the current service. |

Do not merge either historical branch wholesale. The safe migration is to keep
the current detector and MQTT contracts, then bring over the small, testable
calibration/projection pieces and adapt their outputs to the current topics.

## Goal and boundary

Add deterministic visual calibration so a zero-shot detection can produce:

```text
detected object → calibrated image cell → base rotation (x) + IK distance (y)
```

The first motion milestone always sends `z_height = 0`. The future depth model
will supply the vertical `z` value; it must not change the nine-point x/y
mapping or turn relative depth output into millimeters without a separate
depth-scale calibration.

Keep preview/detection-only requests non-moving. Motion must require an explicit
execution request and pass all readiness and bounds checks.

## Current MQTT path to preserve

The current service already uses one `action_id` across the photo lifecycle:

```text
Studio
  → {robot}/commands       JSON detect_object or calibrate_depth request
ESP32
  → {robot}/events         lifecycle and failure events
ESP32
  → {robot}/photos         binary desk_buddy.photo.v1 JPEG frame
Vision worker
  → {robot}/vision         correlated JSON result
Studio
  → displays result or starts an explicitly requested motion sequence
```

The existing worker already validates the binary JPEG envelope and correlates
robot requests, but currently admits only `detect_object` and returns only
`stage: "detection_only"`. Firmware already accepts `calibrate_depth` as a
photo action, so this milestone should not require a firmware protocol change.

## Implementation phases

### 1. Add a visual-calibration operation to the current worker

- Extend the worker request record with an operation type, while retaining the
  same route/action ID/photo correlation and timeout behavior.
- Accept `calibrate_depth` on the robot command route and route its photo to the
  calibration path instead of the zero-shot detector.
- Preserve the raw JPEG validation already used by `detect_object`; reject a
  missing, mismatched, malformed, or reused action ID.
- Publish a terminal result on the robot's existing `{robot}/vision` topic with
  `type: "calibrate_depth"`, a calibration schema/version, the nine points,
  the profile ID, the generated grid, and a structured error on failure.
- Keep calibration state out of MQTT payload credentials and out of model
  manifests. Persist one active profile per robot in Vision-owned application
  data, using an atomic JSON write so worker restarts do not lose calibration.
- Return enough profile metadata for Studio to show which robot and image the
  profile belongs to. Studio may cache the display copy but should not become
  the service's only source of truth.

### 2. Port and harden the nine-point geometry

Start from these historical files:

```text
bigger-rewrite-vision:
  studio/services/vision/ik_control/calibration.py
  studio/services/vision/ik_control/calibration_photo.py
```

Adapt them to the current worker package and keep the implementation
dependency-light. Pillow is already in the managed worker requirements; do not
pull the old OpenCV coordinator or its larger worker stack into the detector.

Calibration assumptions:

- Three paper lanes are the robot's `-30°`, `0°`, and `+30°` base directions.
- Each lane has dots at `0`, `60`, and `120 mm` from the robot base.
- Image coordinates use the camera's pixel coordinate system (`x` right,
  `y` down). The nearest/zero-distance row is therefore normally lower in the
  image than the 120 mm row.
- The nine dots are not expected to form a rectangular image grid. The mono
  camera makes the physical rays appear curved and cone-like.

Detection and validation:

- Detect dark, compact, approximately circular connected components.
- Require exactly nine usable dots; reject missing, extra, overlapping,
  ambiguous, or degenerate layouts rather than silently selecting nine from a
  larger candidate set.
- Assign dots to the three x-ordered lanes and sort each lane by image y to
  obtain depths `0/60/120`.
- Validate lane ordering, minimum separation, row separation, finite numeric
  coordinates, and image bounds.
- Save the original points plus detector parameters and a schema/profile ID so
  recalibration is auditable and reproducible.

Grid construction:

- Use the nine anchors to interpolate image position over angle and distance.
- Preserve the historical quadratic interpolation because it models the
  curved camera projection better than a rectangle or a single affine
  transform.
- Generate a `5° × 5 mm` grid over `-30..+30°` and `0..120 mm`: 12 angle
  intervals × 24 distance intervals = 288 quadrilateral cells.
- Reject degenerate cells and persist each cell's polygon, bounds, angle range,
  distance range, and command values.
- Draw the grid and labels into an annotated preview for calibration UI/debug
  use, while keeping the original JPEG unchanged.

### 3. Project zero-shot detections into the grid

For the selected highest-confidence detection, use the box's horizontal center
and a table-contact proxy near its lower edge:

```text
reference_x = (x0 + x1) / 2
reference_y = y1 - 0.10 × (y1 - y0)
```

This uses the object center in x, but avoids using the visual center in y when
the object sits on the table. If physical tests show that the object center is
the intended point, make the y fraction configurable and record it in the
profile/result rather than changing the geometry silently.

For the reference point:

- Find the containing calibrated polygon.
- Return the cell's representative `angle_deg` and `distance_mm`.
- Include the reference pixel, cell ID, profile ID, calibration status, and
  `z_height_mm: 0` in the result.
- Reject points outside the calibrated polygon. Do not clamp or choose the
  nearest cell for physical motion by default; extrapolation can be a later,
  explicitly opt-in diagnostic mode only.
- If no profile exists, calibration is invalid, the selected detection is
  missing, or any value is non-finite, publish a structured rejection and send
  no motion command.

The detection result should continue to contain every zero-shot box for the UI,
but only the selected box should produce the candidate IK target.

### 4. Connect the target to safe IK control

Keep image math and physical command sequencing separate:

- The Vision worker computes calibration and returns a candidate target.
- The Studio Vision controller/manager owns the explicit motion request and
  remains the only component that sends physical robot commands. This matches
  the current preview architecture and avoids making model inference an
  implicit motion side effect.
- Before motion, request `calibrationvalues` and require
  `motion_calibration_ready:true` from firmware.
- Validate finite values, `rotation_deg` within `±45°`, distance within the
  robot's supported range (`0..180 mm` unless firmware defines a tighter
  calibrated range), a known active profile, and `z_height_mm == 0` for this
  milestone.
- Publish at most one active sequence per robot. Use a fresh child action ID
  for every command and wait for the exact matching firmware terminal event:

  1. `baseRotate` with relative `DEGREES` in the calibrated lane direction;
  2. `controlik` with `distance = distance_mm` and `z_height = 0`;
  3. optional `gripper` action only when the caller explicitly requested grab.

- Abort on publish failure, a mismatched event, firmware failure, timeout, or
  lost MQTT connection. Never retry a command whose physical outcome is
  uncertain.
- Publish a final correlated Vision result only after the requested sequence
  has completed. A preview must never publish `baseRotate`, `controlik`, or
  `gripper`.

Use the current `{robot}/commands` and `{robot}/events` topics. The old
`vision_services` `/test` topic and its coordinator envelope are reference
history, not current integration targets.

### 5. Add the calibration UI/state path

- Add a Visual Calibration action that sends `calibrate_depth`, waits for the
  correlated photo/result, and displays the nine points, profile ID, and grid
  overlay.
- Show the active profile per robot and make calibration invalidation visible
  when a new robot/camera/profile is selected.
- Show the projected reference point and selected cell in the detection preview.
- Keep model installation/readiness controls separate from calibration state;
  a calibration can be generated without running zero-shot inference.
- Make the motion action explicit (for example, **Preview target** versus
  **Move/grab**) and display the target and all safety checks before movement.

### 6. Depth-model follow-up

Next week's depth work should attach to the already validated detection and
calibration result:

```text
same JPEG → zero-shot box + depth map → calibrated x/y + depth-derived z
```

The depth worker must declare its model/version, map direction, units, and
confidence/validity. Use the table-contact reference point or a robust patch
near it, convert relative depth to a robot-specific z estimate through a
separate calibration step, and re-run the final IK bounds check. Until that
scale is validated, `z_height_mm` remains exactly zero.

## Tests and release gates

Add tests before enabling physical execution:

- Repeatable synthetic nine-dot JPEG calibration.
- Missing, extra, overlapping, out-of-order, and degenerate dot rejection.
- Correct point labeling for all three lanes and all three depths.
- Exact 288-cell grid, non-degenerate polygons, and boundary-cell behavior.
- Detection reference-point calculation and calibrated/outside-grid results.
- Worker MQTT correlation for `calibrate_depth`, including photo-before-result
  and result-before-photo ordering, duplicate IDs, malformed frames, timeout,
  and reconnect behavior.
- Profile persistence and replacement per robot.
- No motion for missing calibration, outside-grid targets, bad numeric values,
  unready firmware, or failed detection.
- Exact command order, unique child IDs, acknowledgement matching, one active
  sequence, timeout abort, and no retry.
- Manual gate with a real stencil photo, a real zero-shot detection, and a
  low-speed robot test at known lane/depth cells before enabling grab.

## Completion definition for this slice

The slice is complete when a real `calibrate_depth` capture creates a persisted
nine-point profile, a real zero-shot `detect_object` result projects an object
inside that profile to a bounded base angle and IK distance, and an explicit
preview/motion test can show or execute the `baseRotate` → `controlik` sequence
with `z_height: 0` while all failure paths remain non-moving.
