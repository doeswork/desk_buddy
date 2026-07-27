# Buddy ESP32 MQTT Message Guide

The firmware listens for JSON commands on the status topic (see below), replies with an `in_progress` message, then finishes with `completed` (or `completed` with data). Every command should include a unique `action_id` so you can correlate responses. The firmware ignores any message whose `sender` is `"firmware"`.

- Command topic: `esp32_5/test`
- Heartbeat topic: `esp32_5/HEARTBEAT`
- Typical fields in replies: `sender:"firmware"`, `action_id`, `status:"in_progress"| "completed"| "failed"`, optional `type`, optional data object.

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

Calibrate the base rotation profile. GPIO14 true north is treated as a
momentary active-low pulse from the bump tooth. Calibration uses the saved
neutral servo angle, defaulting to 90, with a fixed slow offset of 10. It first
seeks the true-north pulse, then runs at least two full left/right measurement
passes. After each pass it compares the left and right full-revolution times
and adjusts neutral by 1 to 3 degrees until they are within 2 seconds, with a
maximum of 8 passes. If it cannot balance, the previous calibration is restored.
After each accepted true-north pulse, calibration reverses direction. Any
immediate re-hit of the same marker is ignored until the base has traveled a
meaningful distance, so a bump right next to the switch is not mistaken for a
full revolution.
The `speed` field is ignored for this command so calibration stays slow and
constant.
```json
{"action":"baseRotate","action_id":"71","controlType":"CALIBRATE_PROFILE","sender":"ai_server"}
```

Optional neutral override for continuous servos whose neutral is not the saved value:
```json
{"action":"baseRotate","action_id":"71n","controlType":"CALIBRATE_PROFILE","neutralServoAngle":90,"sender":"ai_server"}
```

Backward-compatible alias
```json
{"action":"baseRotate","action_id":"71b","controlType":"CALIBRATE_BOTH","sender":"ai_server"}
```

After the normal left/right neutral calibration, firmware learns and saves a
balanced `veryslow` angle for each direction. It then verifies one additional
full revolution left and right at those learned speeds. The final timing must
be balanced, and each final encoder count must remain within 5 percent of the
directional count learned by the primary calibration. A mismatch fails
calibration and restores the previous saved profile.

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
- `controlType`: `"STATUS" | "HOME" | "CALIBRATE" | "CALIBRATE_PROFILE" | "CALIBRATE_BOTH" | "STEPS" | "ENCODER"`
- `direction`: `"LEFT" | "RIGHT"` for commands that move in a specified direction
- `speed`: `"veryslow" | "slow" | "regular" | "fast" | "superfast"`; base rotation initially uses neutral offsets of 8, 12, 18, 20, and 30. Profile calibration may independently move the left/right `veryslow` angles toward neutral, and those learned angles are persisted. For `"ENCODER"`/`"STEPS"`, omitted speed defaults to `slow`; explicit speed values are honored.
- `value`: firmware steps when `controlType` is `"ENCODER"`; 216 steps is one full base rotation.
- `steps`: optional alias for `value` when `controlType` is `"STEPS"`
- `neutralServoAngle`: optional neutral override for `"CALIBRATE_PROFILE"` or `"CALIBRATE_BOTH"`
- `calibrated:false` no longer blocks `"ENCODER"`/`"STEPS"` movement; firmware uses estimated counts until real calibration succeeds.

Reply example:
```json
{
  "sender":"firmware",
  "action_id":"69",
  "status":"completed",
  "base_rotation":{
    "calibrated":true,
    "positionTrusted":true,
    "baseAngleDegrees":123.4,
    "basePositionCounts":11234,
    "leftCountsPerRev":32810,
    "rightCountsPerRev":32690,
    "driveGearTeeth":18,
    "baseGearTeeth":108,
    "baseStepsPerRev":216,
    "leftCountsPerStep":152,
    "rightCountsPerStep":151,
    "averageCountsPerStep":152,
    "estimatedCountsPerRev":24576,
    "estimatedCountsPerStep":114,
    "usingEstimatedStepCounts":false,
    "neutralServoAngle":90,
    "calibrationDriveOffset":10,
    "calibrationLeftAngle":80,
    "calibrationRightAngle":100,
    "calibrationPasses":2,
    "calibrationLastDiffMs":500,
    "calibrationBalanced":true,
    "calibrationPhase":"complete",
    "calibrationLastPulseAccepted":true,
    "calibrationLastPulseMs":5100,
    "calibrationLastPulseCounts":32690,
    "calibrationLastPulseMinCounts":16345,
    "calibrationIgnoredPulseCount":1,
    "leftFullRevMs":5600,
    "rightFullRevMs":5100,
    "trueNorthPressed":false,
    "trueNorthPinLevel":1,
    "trueNorthHitCount":3,
    "rawEncoder":2048,
    "speedProfile":{
      "slow":{"left":85,"right":95}
    }
  }
}
```

---
## Gripper — `action: "gripper"`
Commands
```json
{"action":"gripper","action_id":"10","command":"GRAB","sender":"ai_server"}
{"action":"gripper","action_id":"11","command":"DROP","sender":"ai_server"}
{"action":"gripper","action_id":"12","command":"SOFTHOLD","sender":"ai_server"}
```

Position
```json
{"action":"gripper","action_id":"13","position":120,"speed":10,"sender":"ai_server"}
```
- `position`: 0–180, `speed`: delay per degree (ms)

---
## Servo Angles — `action: "servo"`
Set a single arm servo.
```json
{"action":"servo","action_id":"20","servoName":"ELBOW","position":135,"speed":10,"sender":"ai_server"}
```
- `servoName`: `"ELBOW" | "WRIST" | "TWIST"`
- `position`: 0–180, `speed`: delay per degree (ms)

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

Firmware prompts each point with: `Are you ready for peg hole grab attempt at
{depth}mm and {degree} degrees, z={height}mm?` After placing the requested peg,
run the current point:
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
```json
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
Stores values in preferences. Replies with `completed` and an object showing what was saved.

Hover snapshots (saves current ELBOW/WRIST/TWIST + distance)
```json
{"action":"calibrate","action_id":"50","calibration_type":"hover_over_min","distance":20,"sender":"ai_server"}
{"action":"calibrate","action_id":"51","calibration_type":"hover_over_mid","distance":60,"sender":"ai_server"}
{"action":"calibrate","action_id":"52","calibration_type":"hover_over_max","distance":110,"sender":"ai_server"}
```
- Uses current servo angles stored in prefs; requires `distance`.

Hover snapshots for the upper z edge, currently z=50mm (used by `controlik` when `z_height > 0`). The `hover_*_120` names are kept for MQTT compatibility.
```json
{"action":"calibrate","action_id":"50b","calibration_type":"hover_min_120","distance":30,"sender":"ai_server"}
{"action":"calibrate","action_id":"51b","calibration_type":"hover_mid_120","distance":75,"sender":"ai_server"}
{"action":"calibrate","action_id":"52b","calibration_type":"hover_max_120","distance":120,"sender":"ai_server"}
```
- Same idea as hover_over_* but calibrated with the gripper ~50mm above the table. The min point is the first reachable high point, not the table-plane min.

Perch value writes
```json
{"action":"calibrate","action_id":"53","calibration_type":"perch_elbow_angle","value":125,"sender":"ai_server"}
{"action":"calibrate","action_id":"54","calibration_type":"perch_wrist_angle","value":95,"sender":"ai_server"}
{"action":"calibrate","action_id":"55","calibration_type":"perch_twist_angle","value":90,"sender":"ai_server"}
{"action":"calibrate","action_id":"56","calibration_type":"perch_min","value":0,"sender":"ai_server"}
{"action":"calibrate","action_id":"57","calibration_type":"perch_mid","value":50,"sender":"ai_server"}
{"action":"calibrate","action_id":"58","calibration_type":"perch_max","value":100,"sender":"ai_server"}
```
- `value` (or `distance` as fallback) is required.
- Stored under short keys internally; the reply echoes the long names shown above.

Base rotation profile alias
```json
{"action":"calibrate","action_id":"59","calibration_type":"base_rotation_profile","sender":"ai_server"}
{"action":"calibrate","action_id":"59n","calibration_type":"base_rotation_profile","neutralServoAngle":90,"sender":"ai_server"}
```

---
## Calibration Values Dump — `action: "calibrationvalues"`
Returns all stored calibration-related prefs as a `calibrationvalues` object. Missing saved keys are reported as `null`, with additive readiness flags and effective defaults for firmware fallback values. If publish fails you’ll receive a `status:"failed"`.
```json
{"action":"calibrationvalues","action_id":"check_cal","sender":"ai_server"}
```
Reply example:
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
    "hover_over_min":{"ELBOW":..., "WRIST":..., "TWIST":..., "DISTANCE":...},
    "hover_over_mid":{...},
    "hover_over_max":{...},
    "hover_min_120":{...},
    "hover_mid_120":{...},
    "hover_max_120":{...},
    "ik_hover_calibrated":true,
    "ik_z120_calibrated":false,
    "ik_z50_calibrated":false,
    "ik_hover_source":"saved",
    "ik_z120_source":"optional_not_saved",
    "ik_z50_source":"optional_not_saved",
    "perch_configured":true,
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
These send `in_progress` (with optional `phrase`/`log`), then a `completed` with data or streamed photo payloads as implemented in the photo action.

---
## Heartbeat
When enabled, the firmware publishes periodic telemetry on `esp32_5/HEARTBEAT`, including current servo/gripper angles and a timestamp. Hover snapshots are also periodically reported on the status topic when heartbeat is active.

---
## OTA Firmware Update — `action: "ota_update"`
Trigger a remote firmware update over the internet. The ESP32 downloads firmware from a URL (typically GitHub Releases) and installs it.

Basic update (public URL)
```json
{"action":"ota_update","action_id":"update_001","url":"https://github.com/user/repo/releases/download/v1.0.0/firmware.bin","sender":"rails_app"}
```

With authentication (private repo)
```json
{"action":"ota_update","action_id":"update_002","url":"https://github.com/user/repo/releases/download/v1.0.0/firmware.bin","token":"ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx","sender":"rails_app"}
```

With SHA256 verification (recommended)
```json
{"action":"ota_update","action_id":"update_003","url":"https://github.com/user/repo/releases/download/v1.0.0/firmware.bin","token":"ghp_xxxx","sha256":"abc123def456...","version":"v1.0.0","sender":"rails_app"}
```

Fields:
- `url`: Firmware download URL (required)
- `token`: GitHub token for private repos (optional)
- `sha256`: SHA256 hash for verification (optional but recommended)
- `version`: Version identifier for logging (optional)

After a successful update, the ESP32 will reboot automatically. See `GITHUB_OTA_GUIDE.md` for complete setup instructions.

---
## Response Patterns
- Each valid command → `in_progress` then `completed`. If something goes wrong mid‑send (e.g., MQTT publish fails), you’ll get `status:"failed"` with the same `action_id`.
- `type` in replies mirrors the action or calibration subtype (e.g., `perch_elbow_angle`, `calibrationvalues`, `detect_object`). Data objects appear under their own keys (`base_rotation`, `calibrationvalues`, etc.).

Use these templates directly or adapt them to your tooling to drive the ESP32 over MQTT. Ensure every command sets a unique `action_id` so you can pair requests and replies.
