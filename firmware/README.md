# Desk Buddy ESP32-S3 Firmware Guide

The firmware listens for JSON commands on the command topic, publishes lifecycle replies on the event topic, and keeps photos and heartbeat telemetry on their own channels. Every command must include a nonempty `sender` and `action_id`; use a unique ID so replies can be correlated.

- Command topic: `{mqtt_user}/commands` (example: `esp32_5/commands`)
- Event topic: `{mqtt_user}/events`
- Photo topic: `{mqtt_user}/photos`
- Vision result topic: `{mqtt_user}/vision` (reserved for the external Vision service)
- Heartbeat topic: `{mqtt_user}/heartbeat`
- Typical fields in replies: `sender:"firmware"`, `action_id`, `status:"in_progress"| "completed"| "failed"`, optional `type`, optional data object.

For the complete current MQTT contract, see [`MQTT_SPEC.md`](MQTT_SPEC.md).

## Firmware Pinout

These are all GPIOs assigned by the firmware. Camera `PWDN` and `RESET` are `-1`, so those camera signals are not connected to an ESP32 GPIO in this configuration. No UART or USB pins are configured by application code; `Serial` mapping comes from the selected Arduino board/core settings. GPIO 19/20 are explicitly assigned to the TWIST and gripper servos in the table below. On an ESP32-S3 those pins are also the normal native USB D-/D+ pair, so native USB must not be enabled on them unless the hardware or servo mapping is changed.

| GPIO | Firmware use | Direction / electrical behavior |
| ---: | --- | --- |
| 0 | BOOT button / connection reset | Input with pull-up; active low; hold 3 seconds to clear Wi-Fi and MQTT settings |
| 1 | AS5600 analog encoder output | 12-bit analog input |
| 4 | Camera SCCB/SIOD | Camera control data |
| 5 | Camera SCCB/SIOC | Camera control clock |
| 6 | Camera VSYNC | Camera input |
| 7 | Camera HREF | Camera input |
| 8 | Camera D2 / `Y4` | Camera input |
| 9 | Camera D1 / `Y3` | Camera input |
| 10 | Camera D3 / `Y5` | Camera input |
| 11 | Camera D0 / `Y2` | Camera input |
| 12 | Camera D4 / `Y6` | Camera input |
| 13 | Camera PCLK | Camera input |
| 14 | Base true-north bump/tooth input | Input with pull-up; active low; falling-edge interrupt |
| 15 | Camera XCLK | 20 MHz camera clock output |
| 16 | Camera D7 / `Y9` | Camera input |
| 17 | Camera D6 / `Y8` | Camera input |
| 18 | Camera D5 / `Y7` | Camera input |
| 19 | Arm TWIST servo signal | Servo PWM output |
| 20 | Gripper servo signal | Servo PWM output |
| 21 | Base continuous-rotation servo signal | Servo PWM output |
| 39 | Arm WRIST servo signal | Servo PWM output |
| 41 | Firmware status LED | Digital output |
| 45 | Gripper stop input 1 | Input with pull-up; active low |
| 46 | Gripper stop input 2 | Input with pull-up; active low |
| 47 | Arm ELBOW servo signal | Servo PWM output |

The camera data names above follow the `camera_pins.h` mapping used by the ESP32-S3-EYE configuration. GPIO 4/5 are camera SCCB control lines, not a separately configured external I²C bus.

---

## Base Rotate — `action: "baseRotate"`

Control the base using the AS5600 analog encoder and the true-north button. Replies include a `base_rotation` object in the completed or failed message.

Status
```json
{"action":"baseRotate","action_id":"69","controlType":"STATUS","sender":"ai_server"}
```

Home to true north
```json
{"action":"baseRotate","action_id":"70","controlType":"HOME","direction":"RIGHT","speed":"slow","sender":"ai_server"}
```

Calibrate the base rotation profile with the preferred dedicated action. GPIO14 true north is an active-low input
from the bump tooth. The profile uses the supplied neutral angle, or 90 when
omitted, and drives at a fixed 18-degree offset from neutral. It homes to true
north, measures one complete right revolution and one complete left revolution,
then searches for a stable true-neutral angle within 70–110. The two measured
counts-per-revolution values are kept independently; their elapsed times do
not need to match. `speed` is ignored for this command.

```json
{"action":"calibrate_base_rotation","action_id":"71","neutralServoAngle":90,"sender":"ai_server"}
```

For the dedicated action, send `neutralServoAngle` as a JSON integer. If it is omitted—or sent as a string or another JSON type—the run starts from `90`.

Legacy `baseRotate` forms remain supported:

```jsonl
{"action":"baseRotate","action_id":"71a","controlType":"CALIBRATE_PROFILE","neutralServoAngle":90,"sender":"ai_server"}
{"action":"baseRotate","action_id":"71b","controlType":"CALIBRATE_BOTH","sender":"ai_server"}
```

If the encoder cannot confirm a stable neutral, the count calibration still
remains usable; the servo is stopped and the entered neutral is retained.

Rotate by firmware steps. There are 216 firmware steps per full base rotation,
so 2 firmware steps are about 1 physical tooth on the 108-tooth base gear.
`ENCODER` uses firmware steps, not raw AS5600 counts. If calibration exists,
steps use the calibrated left/right counts. If calibration does not exist,
firmware falls back to gear math: `4096 * (108 / 18) = 24576` estimated encoder
counts per full base rotation, or about `114` counts per firmware step. For
`ENCODER`/`STEPS`, omitted `speed` defaults to `slow` so Rails commands move
reliably; explicit speed values are still honored.
```json
{"action":"baseRotate","action_id":"74","controlType":"ENCODER","direction":"RIGHT","speed":"veryslow","value":10,"sender":"ai_server"}
```

Rails-compatible movement with no calibration preflight:
```json
{"sender":"desk_buddy_web","action_id":1683,"action":"baseRotate","workflow_id":null,"workflow_event_id":null,"controlType":"ENCODER","direction":"RIGHT","value":10}
```

Same movement using the default step speed:
```json
{"action":"baseRotate","action_id":"74b","controlType":"ENCODER","direction":"RIGHT","value":10,"sender":"ai_server"}
```

Backward-compatible step alias:
```json
{"action":"baseRotate","action_id":"73c","controlType":"STEPS","direction":"LEFT","speed":"veryslow","steps":6,"sender":"ai_server"}
```

Fields:

- `controlType`: `"STATUS" | "HOME" | "CALIBRATE" | "CALIBRATE_PROFILE" | "CALIBRATE_BOTH" | "STEPS" | "ENCODER" | "DEGREES" | "ANGLE"`
- `direction`: `"LEFT" | "RIGHT"` for commands that move in a specified direction
- `speed`: `"veryslow" | "slow" | "regular" | "fast" | "superfast"`; fixed neutral offsets are 8, 12, 18, 20, and 30 degrees. For `"ENCODER"`/`"STEPS"`, omitted speed defaults to `slow`; other controls default to `veryslow`.
- `value` or `steps`: positive firmware-step count for either `"ENCODER"` or `"STEPS"`; if both are present, `value` takes precedence. There are 216 steps per full base rotation.
- `neutralServoAngle`: optional neutral override for `"CALIBRATE_PROFILE"` or `"CALIBRATE_BOTH"`
- `calibrated:false` no longer blocks `"ENCODER"`/`"STEPS"` movement; firmware uses estimated counts until real calibration succeeds.
- `DEGREES` requires calibrated counts; `ANGLE` requires calibrated counts and a trusted position. `ANGLE` accepts a target in `[0,360)` and applies the saved stencil rotation offset.

Representative reply example (runtime counts, timing, angles, and diagnostics vary by device):

```json
{
  "sender":"firmware",
  "action_id":"69",
  "status":"completed",
  "base_rotation":{
    "calibrated":true,
    "profileCalibrated":true,
    "positionTrusted":true,
    "baseAngleDegrees":123.4,
    "basePositionCounts":11234,
    "leftCountsPerRev":32810,
    "rightCountsPerRev":32690,
    "driveGearTeeth":18,
    "baseGearTeeth":108,
    "baseStepsPerRev":216,
    "encoderSign":-1,
    "leftCountsPerStep":152,
    "rightCountsPerStep":151,
    "averageCountsPerStep":152,
    "estimatedCountsPerRev":24576,
    "estimatedCountsPerStep":114,
    "usingEstimatedStepCounts":false,
    "rotationOffsetDegrees":1.2,
    "neutralServoAngle":90,
    "calibrationDriveOffset":18,
    "calibrationLeftAngle":72,
    "calibrationRightAngle":108,
    "calibrationPasses":2,
    "calibrationLastDiffMs":500,
    "calibrationBalanced":false,
    "calibrationPhase":"complete",
    "calibrationLastPulseAccepted":true,
    "calibrationLastPulseMs":5600,
    "calibrationLastPulseCounts":32810,
    "calibrationLastPulseMinCounts":0,
    "calibrationIgnoredPulseCount":0,
    "leftFullRevMs":5600,
    "rightFullRevMs":5100,
    "verySlowValidated":true,
    "trueNorthPressed":false,
    "trueNorthPinLevel":1,
    "trueNorthHitCount":3,
    "rawEncoder":2048,
    "speedProfile":{
      "veryslow":{"left":82,"right":98},
      "slow":{"left":78,"right":102},
      "regular":{"left":72,"right":108},
      "fast":{"left":70,"right":110},
      "superfast":{"left":60,"right":120}
    }
  }
}
```

---

## Gripper — `action: "gripper"`

Commands

```jsonl
{"action":"gripper","action_id":"10","command":"GRAB","sender":"ai_server"}
{"action":"gripper","action_id":"11","command":"DROP","sender":"ai_server"}
{"action":"gripper","action_id":"12","command":"SOFTHOLD","sender":"ai_server"}
```

Position

```json
{"action":"gripper","action_id":"13","position":120,"speed":10,"sender":"ai_server"}
```

- `position`: any nonnegative integer; values above 180 are clamped to 180
- `speed`: delay per degree in milliseconds; default `10`. The firmware does not validate its range, so use a nonnegative value.

---

## Servo Angles — `action: "servo"`

Set a single arm servo.

```json
{"action":"servo","action_id":"20","servoName":"ELBOW","position":135,"speed":10,"sender":"ai_server"}
```

- `servoName`: `"ELBOW" | "WRIST" | "TWIST"`
- `position`: integer from 0–180
- `speed`: accepted but ignored; the firmware's easing profile controls normal movement speed

---

## Inverse Kinematics — `action: "controlik"`

Move arm based on distance and z height. Nonzero z uses the calibrated trapezoid workspace.

```json
{"action":"controlik","action_id":"30","distance":85.0,"z_height":0.0,"sender":"ai_server"}
```

- `distance`: float (required)
- `z_height`: float mm (optional, default 0; valid range 0–50; nonzero values require legacy-named hover_*_120 calibrations for the z=50 top edge)
- Saved stencil calibration offset `ik_off_mm` is added to `distance` at runtime and clamped to `>= 0`.
- Requests outside the trapezoid fail. For example, if z=0 min is 0mm and z=50 min is 30mm, then `distance:0,z_height:50` fails while `distance:15,z_height:25` is on the slanted boundary.

---

## Stencil Calibration — `action: "stencilCalibrate"`

Human-in-the-loop calibration using 15 stencil checks. `START` moves the arm to
perch, homes the base to true north by rotating `RIGHT` at `veryslow`, then the session runs:

- z=0 offset points at `-30`, `0`, and `+30` degrees with distances `0`, `60`, `120` mm
- z=50 center validation points at distances `30`, `75`, `120` mm
- z=25 center validation points at distances `15`, `60`, `120` mm

Firmware prompts for each peg, moves to perch before required lane rotations,
moves base only when the stencil lane angle changes, moves IK, tries a gripper
`GRAB`, accepts numeric nudges, then saves all point diagnostics plus averaged
global offsets from the 9 z=0 offset points only.
Repeated same-lane `RUN_POINT` commands skip base rotation unless
`rotationNudgeDegrees` changes the target angle.

Start a session:

```json
{"action":"stencilCalibrate","action_id":"stencil_start","command":"START","sender":"ai_server"}
```

Each prompt begins `Are you ready for peg hole grab attempt at {depth}mm and
{degree} degrees, z={height}mm?`, then identifies the point and asks for
`RUN_POINT`. After placing the requested peg, run the current point:

```json
{"action":"stencilCalibrate","action_id":"stencil_run_001","command":"RUN_POINT","sender":"ai_server"}
```

If the grab misses, apply numeric nudges. `ADJUST` updates the target and
returns a new ready prompt; send `RUN_POINT` again when the peg is ready:

```json
{"action":"stencilCalibrate","action_id":"stencil_adjust_001","command":"ADJUST","rotationNudgeDegrees":2.0,"distanceNudgeMm":-3.0,"sender":"ai_server"}
```

If the grab succeeds but the target was still visibly off, adjust and retry the
most recently completed point:

```json
{"action":"stencilCalibrate","action_id":"stencil_adjust_previous_001","command":"ADJUST_PREVIOUS","rotationNudgeDegrees":-1.0,"distanceNudgeMm":0.0,"sender":"ai_server"}
```

Other commands:

```jsonl
{"action":"stencilCalibrate","action_id":"stencil_status","command":"STATUS","sender":"ai_server"}
{"action":"stencilCalibrate","action_id":"stencil_cancel","command":"CANCEL","sender":"ai_server"}
{"action":"stencilCalibrate","action_id":"stencil_clear","command":"CLEAR","sender":"ai_server"}
```

Nudge meanings:

- `rotationNudgeDegrees > 0`: target farther RIGHT
- `rotationNudgeDegrees < 0`: target farther LEFT
- `distanceNudgeMm > 0`: reach farther/deeper
- `distanceNudgeMm < 0`: reach shallower/closer

Saved preferences:

- `st_map`: compact JSON with all 15 point corrections and validation diagnostics
- `rot_off_deg`: average z=0 offset-point rotation nudge, applied to absolute base `ANGLE` commands
- `ik_off_mm`: average z=0 offset-point distance nudge, applied to `controlik.distance`

---

## Perch Pose — `action: "perch"`

Moves arm to stored perch angles.

```json
{"action":"perch","action_id":"40","sender":"ai_server"}
```

Uses perch calibration values (see below).

---

## Calibration — `action: "calibrate"`

Stores values in Preferences or runs the legacy base profile path. Successful writes return `completed` with an object showing what was saved; validation, motion, or storage failures return `failed`.

Hover snapshots (saves current ELBOW/WRIST/TWIST + distance)

```jsonl
{"action":"calibrate","action_id":"50","calibration_type":"hover_over_min","distance":20,"sender":"ai_server"}
{"action":"calibrate","action_id":"51","calibration_type":"hover_over_mid","distance":60,"sender":"ai_server"}
{"action":"calibrate","action_id":"52","calibration_type":"hover_over_max","distance":110,"sender":"ai_server"}
```

- Uses current servo angles stored in prefs; requires `distance`.

Hover snapshots for the upper z edge, currently z=50mm (used by `controlik` when `z_height > 0`). The `hover_*_120` names are kept for MQTT compatibility.

```jsonl
{"action":"calibrate","action_id":"50b","calibration_type":"hover_min_120","distance":30,"sender":"ai_server"}
{"action":"calibrate","action_id":"51b","calibration_type":"hover_mid_120","distance":75,"sender":"ai_server"}
{"action":"calibrate","action_id":"52b","calibration_type":"hover_max_120","distance":120,"sender":"ai_server"}
```

- Same idea as hover_over_* but calibrated with the gripper ~50mm above the table. The min point is the first reachable high point, not the table-plane min.

Perch value writes

```jsonl
{"action":"calibrate","action_id":"53","calibration_type":"perch_elbow_angle","value":125,"sender":"ai_server"}
{"action":"calibrate","action_id":"54","calibration_type":"perch_wrist_angle","value":95,"sender":"ai_server"}
{"action":"calibrate","action_id":"55","calibration_type":"perch_twist_angle","value":90,"sender":"ai_server"}
{"action":"calibrate","action_id":"56","calibration_type":"perch_min","value":0,"sender":"ai_server"}
{"action":"calibrate","action_id":"57","calibration_type":"perch_mid","value":50,"sender":"ai_server"}
{"action":"calibrate","action_id":"58","calibration_type":"perch_max","value":100,"sender":"ai_server"}
```

- `value` (or `distance` as fallback) is required.
- Stored under short keys internally; the reply echoes the long names shown above.

Legacy base rotation profile alias (prefer `action:"calibrate_base_rotation"`)

```jsonl
{"action":"calibrate","action_id":"59","calibration_type":"base_rotation_profile","sender":"ai_server"}
{"action":"calibrate","action_id":"59n","calibration_type":"base_rotation_profile","neutralServoAngle":90,"sender":"ai_server"}
```

---

## Calibration Values Dump — `action: "calibrationvalues"`

Returns all stored calibration-related prefs as a `calibrationvalues` object. Missing saved keys are reported as `null`, with additive readiness flags and effective defaults for firmware fallback values. If the detailed publish fails, firmware attempts a smaller `status:"failed"` response; a broken connection can prevent that fallback from arriving.

```json
{"action":"calibrationvalues","action_id":"check_cal","sender":"ai_server"}
```

Representative reply excerpt (the firmware also returns the remaining fields listed in `MQTT_SPEC.md`):

```json
{
  "sender":"firmware",
  "action_id":"check_cal",
  "status":"completed",
  "type":"calibrationvalues",
  "calibrationvalues":{
    "ELBOW_ANGLE":90,"WRIST_ANGLE":90,"TWIST_ANGLE":90,"GRIPPER_ANGLE":180,
    "PERCH_ELBOW_ANGLE":125,"PERCH_WRIST_ANGLE":95,"PERCH_TWIST_ANGLE":90,
    "PERCH_MIN":0,"PERCH_MID":50,"PERCH_MAX":100,
    "hover_over_min":{"ELBOW":126,"WRIST":0,"TWIST":90,"DISTANCE":20},
    "hover_over_mid":{"ELBOW":132,"WRIST":46,"TWIST":90,"DISTANCE":60},
    "hover_over_max":{"ELBOW":165,"WRIST":160,"TWIST":90,"DISTANCE":110},
    "hover_min_120":{"ELBOW":120,"WRIST":30,"TWIST":90,"DISTANCE":30},
    "hover_mid_120":{"ELBOW":135,"WRIST":75,"TWIST":90,"DISTANCE":75},
    "hover_max_120":{"ELBOW":155,"WRIST":130,"TWIST":90,"DISTANCE":120},
    "ik_hover_calibrated":true,
    "ik_z120_calibrated":true,
    "ik_z50_calibrated":true,
    "ik_hover_source":"saved",
    "ik_z120_source":"saved",
    "ik_z50_source":"saved_legacy_keys",
    "perch_configured":true,
    "perch_distance_configured":true,
    "perch_defaults_applied":false,
    "perch_effective":{"ELBOW":125,"WRIST":95,"TWIST":90,"MIN":0,"MID":50,"MAX":100,"source":"saved"},
    "stencil_calibrated":true,
    "stencil_runtime_mode":"average_offsets",
    "rot_off_deg":1.2,
    "ik_off_mm":-2.5,
    "base_rotation_calibrated":true,
    "base_rotation_profileCalibrated":true,
    "base_rotation_ready":true,
    "base_rotation_leftCountsPerRev":32810,
    "base_rotation_rightCountsPerRev":32690,
    "base_rotation_lastCounts":11234,
    "base_rotation_lastValid":true,
    "base_rotation_veryslowValidated":true,
    "motion_calibration_ready":true,
    "initial_calibration_ready":true
  }
}
```

Readiness notes:

- `motion_calibration_ready` requires a profiled/trusted base and saved monotonic `hover_over_min/mid/max`.
- `initial_calibration_ready` also requires saved stencil average offsets.
- `ik_z50_calibrated` is optional and does not block table-level grab flows. Legacy `ik_z120_calibrated` is still reported for compatibility.
- `perch_effective` always reports the values firmware will use; `source:"firmware_default"` means no saved perch angles exist yet.

---

## Photo / Vision

Take a photo

```json
{"action":"photo","action_id":"60","sender":"ai_server"}
```

Object detection (with optional phrase)

```json
{"action":"detect_object","action_id":"61","phrase":["red cup","green bottle"],"sender":"ai_server"}
```

Color detection

```json
{"action":"detect_color","action_id":"62","sender":"ai_server"}
```

Depth calibration capture

```json
{"action":"calibrate_depth","action_id":"63","sender":"ai_server"}
```

These actions attempt `in_progress`, publish the binary JPEG on the photo topic, then send `completed`. Capture failures produce a structured `failed` event when the MQTT connection is still usable; an incomplete stream closes the socket, so that failure can instead be observable only as a disconnect/timeout.

---

## Heartbeat

The firmware attempts one heartbeat immediately after each MQTT connection. When periodic heartbeat is enabled, it then publishes every four seconds on `{mqtt_user}/heartbeat` (for example, `esp32_5/heartbeat`), including saved servo/gripper angles and a timestamp. Missing saved angle keys appear as `0` in heartbeat telemetry. Optional hover snapshots are reported on the event topic.

---

## OTA Firmware Update — `action: "ota_update"`

Trigger a remote firmware update over the internet. The ESP32 downloads firmware from a URL (typically GitHub Releases) and installs it.

Basic update (public URL)

```json
{"action":"ota_update","action_id":"update_001","url":"https://github.com/user/repo/releases/download/v1.0.0/firmware.bin","sender":"rails_app"}
```

With SHA256 verification (recommended)

```json
{"action":"ota_update","action_id":"update_002","url":"https://github.com/user/repo/releases/download/v1.0.0/firmware.bin","sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","version":"v1.0.0","sender":"rails_app"}
```

Fields:

- `url`: Firmware download URL (required)
- `sha256`: 64-hex-character SHA256 hash, optionally prefixed with `sha256:` (optional)
- `version`: Version identifier for logging (optional)

The firmware does not support a `token` or authorization header. An invalid
nonempty SHA256 value is warned about and ignored; a valid value is checked
after the download. After a successful update, the ESP32 reboots automatically.

---

## Response Patterns

- Each recognized action first attempts `in_progress`, followed by an action-specific terminal result when the handler returns. A handler failure normally produces `status:"failed"` with the same `action_id`; an MQTT disconnect can prevent either response from arriving.
- `type` appears in the initial reply for photo actions, `calibrate_base_rotation`, `calibrate` when `calibration_type` is supplied, and `calibrationvalues`. Some terminal paths also include it; use `action_id` for correlation. Detailed data objects appear under their own keys (`base_rotation`, `calibrationvalues`, etc.).

Use these templates directly or adapt them to your tooling to drive the ESP32 over MQTT. Ensure every command sets a unique `action_id` so you can pair requests and replies.
