# Desk Buddy ESP32 MQTT API

This document is the code-facing MQTT contract implemented by the ESP32 firmware. It describes the topics, request and response envelopes, every accepted action, calibration storage and readiness, telemetry, binary photo framing, and known failure behavior.

The firmware source is authoritative if this document and the code disagree. Human-operated calibration steps are documented separately in `CALIBRATE_ROBOT_WALKTHROUGH.md`.

## 1. Connection and Topics

MQTT settings are read at boot from the `mqtt` Preferences namespace.

| Setting | Preference key | Default |
| --- | --- | --- |
| Broker host | `server` | `mqtt.deskbuddy.ai` |
| Broker port | `port` | `8883` |
| Username | `user` | empty; must be configured |
| Password | `password` | empty; must be configured |
| Client ID | `client_id` | empty; must be configured |

The username also identifies the robot's topics:

| Topic | Firmware behavior |
| --- | --- |
| `{mqtt_user}/test` | Subscribes for commands and publishes command responses, ready messages, photos, debug messages, and count messages |
| `{mqtt_user}/HEARTBEAT` | Publishes heartbeat telemetry |

Important transport behavior:

- Commands and responses share `{mqtt_user}/test`. Consumers must filter by `sender`, `action_id`, `status`, or message-specific fields.
- MQTT uses TLS but the ESP32 currently calls `setInsecure()`, so it does not verify the broker certificate.
- PubSubClient uses QoS 0 and non-retained publishes. The subscription also uses QoS 0.
- The ordinary MQTT buffer is `6144` bytes. Command dispatch uses a `512`-byte ArduinoJson document, so requests should remain compact.
- Action handlers are synchronous. A long servo, base calibration, stencil step, photo capture, or OTA operation blocks dispatch and may prevent MQTT maintenance until it returns.
- The receive path uses a single-message slot: one incoming non-count payload wakes the listen loop and is dispatched immediately. The slot is cleared only when its message is copied for dispatch, so a follow-up command received by MQTT maintenance immediately after the previous terminal response is preserved. Send one stateful command at a time and wait for its exact matching terminal response.

## 2. Request Envelope

Commands are JSON objects published to `{mqtt_user}/test`.

```json
{
  "sender": "ai_server",
  "action_id": "unique-command-id",
  "action": "servo"
}
```

### Common request fields

| Field | Requirement | Behavior |
| --- | --- | --- |
| `action` | Required | Exact, case-sensitive action name dispatched by the firmware |
| `action_id` | Strongly recommended | String or JSON integer used to correlate replies; other JSON types are not recognized as IDs |
| `sender` | Recommended | Identifies the caller. Never use `firmware`; messages with `sender:"firmware"` are ignored when recognized |
| `phrase` | Optional | Scalar or array copied to the initial response and to response paths that preserve it |
| `use_model` / `useModel` | Optional | Any JSON value copied as `use_model` to the initial response and photo metadata |
| `workflow_id` | Optional JSON integer | Copied to responses generated during this dispatch |
| `workflow_event_id` | Optional JSON integer | Copied only when `workflow_id` is a JSON integer |

`action_id` is not universally required by the firmware, but omitting it makes correlation unreliable. A known action without an ID receives no initial `in_progress`; some handlers still publish a terminal message with `action_id:""`.

### Dispatch filtering and malformed requests

- A raw payload beginning exactly with `{"count":` is ignored by the MQTT callback.
- The callback tries to parse a `128`-byte JSON document. When parsing succeeds, `sender:"firmware"` is ignored. Other payloads are copied into the single receive slot and parsed again by the dispatcher.
- Valid shared-topic JSON without `action`, invalid JSON, a request too large for the dispatch document, or an unknown/case-mismatched action produces serial diagnostics only.
- Extra fields are generally ignored.

## 3. Response Lifecycle and Envelopes

For a recognized action with an `action_id`, the controller first publishes:

```json
{
  "sender": "firmware",
  "action_id": "unique-command-id",
  "status": "in_progress"
}
```

Most actions later publish one terminal response:

```json
{
  "sender": "firmware",
  "action_id": "unique-command-id",
  "status": "completed"
}
```

or:

```json
{
  "sender": "firmware",
  "action_id": "unique-command-id",
  "status": "failed"
}
```

Detailed responses add a nested result object:

```json
{
  "sender": "firmware",
  "action_id": "base-status-1",
  "status": "completed",
  "base_rotation": {}
}
```

Existing optional response fields include `type`, `phrase`, `workflow_id`, and `workflow_event_id`.

Response nuances:

- `phrase` is always copied to the initial `in_progress`. Final `baseRotate`, `servo`, `controlik`, and `stencilCalibrate` responses also preserve it. Gripper, perch, calibration-value, calibration-write, and OTA terminal paths do not consistently preserve it.
- `use_model` is emitted in the initial `in_progress` and photo metadata, not ordinary terminal responses.
- Workflow fields are injected into normal in-progress, completed/failed, detailed, and photo messages. They are not included in ready, heartbeat, count, or debug messages.
- `type` is included initially for photo actions, an explicitly supplied calibration subtype, and `calibrationvalues`. It is omitted initially for the other actions.
- A detailed-result serialization failure places the raw result string under the result key. A detailed publish failure attempts a smaller `failed` response when an action ID exists.
- A logical operation can complete at the MQTT level while reporting a state that requires more work. In particular, a stencil grab miss returns `status:"completed"` with `phase:"needs_adjustment"`.

### Action and terminal-result summary

| `action` | Terminal behavior | Detailed key |
| --- | --- | --- |
| `gripper` | `completed` or `failed` | none |
| `servo` | `completed` or `failed` | none |
| `baseRotate` | `completed` or `failed` | `base_rotation` |
| `controlik` | `completed` or `failed` | none |
| `perch` | Always publishes `completed` after calling the moves | none |
| `calibrate` | Calibration-specific `completed` or `failed` | saved calibration key or `base_rotation` |
| `calibrationvalues` | `completed`, or `failed` if Preferences cannot open | `calibrationvalues` |
| `stencilCalibrate` | `completed` or `failed` | `stencil_calibration` |
| `photo` | Photo frame, then another `in_progress` with `log:"sent"` | binary photo frame |
| `detect_object` | Same as `photo` | binary photo frame |
| `detect_color` | Same as `photo` | binary photo frame |
| `calibrate_depth` | Same as `photo` | binary photo frame |
| `ota_update` | `failed`, or usually reboot before terminal success | none |

## 4. Motion Actions

### 4.1 `servo`

Moves one arm joint.

```json
{
  "sender": "ai_server",
  "action_id": "servo-1",
  "action": "servo",
  "servoName": "ELBOW",
  "position": 120
}
```

| Field | Requirement | Accepted values |
| --- | --- | --- |
| `servoName` | Required | `ELBOW`, `WRIST`, or `TWIST`, case-insensitive |
| `position` | Required | Integer `0..180` |
| `speed` | Ignored | Servo speed is fixed by the firmware's easing profile |

Normal moves save `ELBOW_ANGLE`, `WRIST_ANGLE`, or `TWIST_ANGLE` in `config` Preferences. Elbow and wrist moves are checked by the table-plane safety guard when valid `hover_over_min/mid/max` data exists.

The special string ID `action_id:"live"` selects live mode. Live mode bypasses the table guard and moves at a constant slow rate. It still persists the resulting angle. Use this only for careful manual calibration or recovery.

### 4.2 `gripper`

The gripper accepts either a named command or a direct position.

```json
{"sender":"ai_server","action_id":"grip-1","action":"gripper","command":"GRAB"}
```

| Form | Behavior |
| --- | --- |
| `command:"GRAB"` | Opens to `180`, closes toward `0`, and succeeds when either stop input is pressed; otherwise reopens and returns `failed` |
| `command:"DROP"` | Opens to `180` and returns `completed` |
| `command:"SOFTHOLD"` | Opens gradually while a stop input remains pressed and returns `completed` |
| `position` | Direct position; negative/missing fails, values above `180` are clamped to `180` |
| `speed` | Optional integer delay in milliseconds per degree for direct-position movement; default `10` |

Named gripper commands are case-sensitive and must use the uppercase spellings above. If `command` is absent or unknown but a nonnegative `position` exists, the position form is used. The result is stored as `GRIPPER_ANGLE`. Gripper operations also emit separate debug messages.

### 4.3 `perch`

```json
{"sender":"ai_server","action_id":"perch-1","action":"perch"}
```

Moves wrist, elbow, and twist to the saved perch pose, in that order. No action-specific fields are read.

| Value | Short storage key | Legacy key | Default |
| --- | --- | --- | --- |
| Elbow angle | `p_elbow` | `PERCH_ELBOW_ANGLE` | `120` |
| Wrist angle | `p_wrist` | `PERCH_WRIST_ANGLE` | `90` |
| Twist angle | `p_twist` | `PERCH_TWIST_ANGLE` | `90` |
| Minimum reach | `p_min` | `PERCH_MIN` | `0` |
| Middle reach | `p_mid` | `PERCH_MID` | `50` |
| Maximum reach | `p_max` | `PERCH_MAX` | `100` |

Only the three angles are moved by this action. The distance values are calibration metadata. The controller publishes `completed` even if an individual guarded servo move was blocked.

### 4.4 `controlik`

```json
{
  "sender": "ai_server",
  "action_id": "ik-1",
  "action": "controlik",
  "distance": 85.0,
  "z_height": 0.0
}
```

| Field | Requirement | Behavior |
| --- | --- | --- |
| `distance` | Required number | Requested radial distance in millimeters; must be `>= 0` |
| `z_height` | Optional number | Height in millimeters, default `0`; must be `>= 0`, and values above `50` fail |

At `z_height:0`, valid saved `hover_over_min/mid/max` points provide piecewise interpolation. If they are missing or invalid, built-in z=0 defaults/direct IK allow basic operation. Distances beyond a calibrated endpoint clamp to that endpoint's arm angles.

For `0 < z_height <= 50`, both the z=0 set and the legacy-named `hover_min_120/mid/max_120` z=50 set must be valid. The allowed reach is the trapezoid between those two calibrated edges; a request outside the height-specific minimum/maximum returns `failed`.

The saved stencil correction `ik_off_mm` is reloaded and added before workspace checks. Missing `ik_off_mm` means a `0 mm` correction. The adjusted distance is clamped to at least zero. Internal stencil-calibration moves explicitly disable this offset.

The IK movement can still fail if the resulting elbow/wrist sequence is blocked by the table guard.

### 4.5 `baseRotate`

```json
{
  "sender": "ai_server",
  "action_id": "base-1",
  "action": "baseRotate",
  "controlType": "STATUS"
}
```

`controlType`, direction, and speed labels are case-insensitive.

| `controlType` | Fields | Behavior |
| --- | --- | --- |
| `STATUS` | none | Updates encoder tracking, corrects position to zero if true north is pressed, and returns status |
| `HOME` | `direction`; optional `speed` | Rotates until the true-north input is detected and makes position trusted |
| `CALIBRATE` | `direction`; optional `speed` | Measures counts per revolution for one direction |
| `CALIBRATE_PROFILE` | optional `neutralServoAngle` | Runs the complete left/right counts, timing, neutral-balance, and speed-profile calibration |
| `CALIBRATE_BOTH` | optional `neutralServoAngle` | Alias for `CALIBRATE_PROFILE` |
| `ENCODER` | `direction`, positive `value`; optional `speed` | Moves a number of firmware base steps; despite the name, `value` is not raw encoder counts |
| `STEPS` | `direction`, positive `value` or `steps`; optional `speed` | Same firmware-step movement as `ENCODER` |
| `DEGREES` | `direction`, positive `value`; optional `speed` | Relative degree movement; requires rotation calibration |
| `ANGLE` | `value` in `[0,360)`; optional `speed` | Shortest-path absolute target; requires calibrated counts and trusted position |

Accepted `direction` values are `LEFT` and `RIGHT`.

Accepted speed labels are `veryslow`, `slow`, `regular`, `fast`, and `superfast`. Missing speed defaults to `slow` for `ENCODER`/`STEPS`, and `veryslow` for the other controls. An unrecognized supplied speed also becomes `veryslow`.

There are `216` firmware steps per base revolution. `ENCODER` and `STEPS` use calibrated directional counts when available; otherwise they use an estimated `24576` counts per revolution. These step modes can therefore operate before calibration, but estimated movement is not precision motion.

`DEGREES` requires at least usable calibrated counts. `ANGLE` additionally requires `positionTrusted:true`. Normal `ANGLE` commands add the saved `rot_off_deg`, normalize to `[0,360)`, and then choose the shortest direction. Missing `rot_off_deg` means a `0°` correction. Internal stencil moves disable this correction.

`neutralServoAngle` is parsed as an integer. The calibration implementation ultimately accepts only `70..110`; values outside that range fail.

Every terminal response contains `base_rotation`. Its fields are:

- State: `calibrated`, `profileCalibrated`, `positionTrusted`, `baseAngleDegrees`, `basePositionCounts`.
- Counts/math: `leftCountsPerRev`, `rightCountsPerRev`, `driveGearTeeth`, `baseGearTeeth`, `baseStepsPerRev`, `encoderSign`, directional/average counts per step, estimated counts fields, and `usingEstimatedStepCounts`.
- Stencil/runtime: `rotationOffsetDegrees`.
- Profile calibration: `neutralServoAngle`, calibration drive/left/right angles, pass count, timing difference, balanced flag, phase, pulse diagnostics, full-revolution timings, and nested `speedProfile` angles.
- Hardware diagnostics: true-north pressed/level/hit count and `rawEncoder`.
- Failure: optional `error` while the outer status is `failed`.

## 5. Calibration API

### 5.1 `calibrate`

This action writes one calibration item or starts the base profile calibration.

```json
{
  "sender": "calibration_tool",
  "action_id": "cal-1",
  "action": "calibrate",
  "calibration_type": "hover_over_mid",
  "distance": 60,
  "ELBOW": 132,
  "WRIST": 46,
  "TWIST": 90
}
```

Calibration type names are case-sensitive.

| `calibration_type` | Required inputs | Stored result |
| --- | --- | --- |
| `hover_over_min` | nonnegative `distance`; optional joint overrides | JSON string under `hover_over_min` |
| `hover_over_mid` | same | JSON string under `hover_over_mid` |
| `hover_over_max` | same | JSON string under `hover_over_max` |
| `hover_min_120` | same | First z=50 upper-edge point under the legacy key |
| `hover_mid_120` | same | Middle z=50 upper-edge point under the legacy key |
| `hover_max_120` | same | Last z=50 upper-edge point under the legacy key |
| `perch_elbow_angle` | `value` or `distance` | Float under `p_elbow` |
| `perch_wrist_angle` | `value` or `distance` | Float under `p_wrist` |
| `perch_twist_angle` | `value` or `distance` | Float under `p_twist` |
| `perch_min` | `value` or `distance` | Float under `p_min` |
| `perch_mid` | `value` or `distance` | Float under `p_mid` |
| `perch_max` | `value` or `distance` | Float under `p_max` |
| `base_rotation_profile` | optional `neutralServoAngle` | Runs the same profile process as `baseRotate/CALIBRATE_PROFILE` |

If `calibration_type` is omitted, it defaults to `hover_over_min`. Unknown types fail.

For hover points, omitted `ELBOW`, `WRIST`, or `TWIST` values come from the last saved arm-angle Preferences, defaulting individually to `90`. Supplied overrides and the final angles are also saved as the current arm angles. The detailed result key is the calibration storage key and contains `ELBOW`, `WRIST`, `TWIST`, and `DISTANCE`.

Perch numeric inputs and hover numeric inputs accept JSON numbers; the current implementation also accepts nonempty strings through numeric conversion. Clients should send actual JSON numbers.

The `hover_*_120` names are compatibility names. They represent the reachable upper edge at z=50 mm, not a 120 mm-high plane.

### 5.2 `calibrationvalues`

```json
{"sender":"calibration_tool","action_id":"check-cal-1","action":"calibrationvalues"}
```

Returns a complete calibration inventory under `calibrationvalues`.

```json
{
  "sender": "firmware",
  "action_id": "check-cal-1",
  "status": "completed",
  "type": "calibrationvalues",
  "calibrationvalues": {}
}
```

#### Returned fields

| Group | Fields |
| --- | --- |
| Current joints | `ELBOW_ANGLE`, `WRIST_ANGLE`, `TWIST_ANGLE`, `GRIPPER_ANGLE`; float or `null` |
| Saved perch | `PERCH_ELBOW_ANGLE`, `PERCH_WRIST_ANGLE`, `PERCH_TWIST_ANGLE`, `PERCH_MIN`, `PERCH_MID`, `PERCH_MAX`; float or `null` |
| Effective perch | `perch_configured`, `perch_distance_configured`, `perch_defaults_applied`, and `perch_effective` with `ELBOW`, `WRIST`, `TWIST`, `MIN`, `MID`, `MAX`, `source` |
| IK points | `hover_over_min`, `hover_over_mid`, `hover_over_max`, `hover_min_120`, `hover_mid_120`, `hover_max_120`; parsed object, `null`, or raw stored string if invalid JSON |
| IK readiness | `ik_hover_calibrated`, `ik_z120_calibrated`, `ik_z50_calibrated`, `ik_hover_source`, `ik_z120_source`, `ik_z50_source` |
| Stencil | `rot_off_deg`, `ik_off_mm`, `st_map`, `stencil_calibrated`, `stencil_runtime_mode` |
| Base rotation | `base_rotation_calibrated`, `base_rotation_profileCalibrated`, `base_rotation_leftCountsPerRev`, `base_rotation_rightCountsPerRev`, `base_rotation_lastCounts`, `base_rotation_lastValid`, `base_rotation_ready` |
| Combined readiness | `motion_calibration_ready`, `initial_calibration_ready` |

`st_map` is returned as a JSON-encoded string, not a nested object.

#### Readiness definitions

| Field | Exact condition |
| --- | --- |
| `perch_configured` | All three saved perch-angle keys exist, using short or legacy keys |
| `perch_distance_configured` | All three saved perch-distance keys exist |
| `ik_hover_calibrated` | All z=0 points exist, contain nonnegative `DISTANCE`, and satisfy min < mid < max |
| `ik_z120_calibrated` / `ik_z50_calibrated` | All legacy z=50 points exist, contain nonnegative `DISTANCE`, and satisfy min < mid < max |
| `stencil_calibrated` | The `st_map`, `rot_off_deg`, and `ik_off_mm` keys all exist; map contents are not validated here |
| `base_rotation_ready` | Calibrated and profile-calibrated flags are true, both directional counts are positive, and last position is trusted |
| `motion_calibration_ready` | `base_rotation_ready && ik_hover_calibrated` |
| `initial_calibration_ready` | `base_rotation_ready && ik_hover_calibrated && stencil_calibrated` |

The z=50 set, perch values, and stencil corrections are not required for basic z=0 motion. Stencil values are required only for `stencil_calibrated` and `initial_calibration_ready`, and are recommended for accurate peg/stencil positioning. Missing `rot_off_deg` and `ik_off_mm` behave as zero at runtime; missing `st_map` does not block motion.

### 5.3 `stencilCalibrate`

The stencil workflow is an interactive, RAM-resident 15-point session. Each command gets its own unique `action_id`.

```json
{"sender":"calibration_tool","action_id":"stencil-start-1","action":"stencilCalibrate","command":"START"}
```

Command names are case-insensitive.

| `command` | Fields | Behavior |
| --- | --- | --- |
| `START` | none | Perches the arm, homes right at `veryslow`, verifies absolute-angle readiness, clears RAM session results, and begins at point 0 |
| `RUN_POINT` | none | Opens the gripper, moves base if needed, moves IK with stored offsets disabled, and attempts a grab |
| `ADJUST` | optional `rotationNudgeDegrees`, `distanceNudgeMm` | Adds deltas to the current point and returns to `place_peg`; absent/invalid deltas behave as zero |
| `ADJUST_PREVIOUS` | optional nudge fields | Reopens the most recently completed point, applies deltas, and immediately retries it |
| `STATUS` | none | Returns current RAM session status and saved averaged offsets |
| `CANCEL` | none | Ends the active RAM session without saving its work; previously saved offsets remain |
| `CLEAR` | none | Ends the session and removes `st_map`, `rot_off_deg`, and `ik_off_mm` from Preferences |

Point order:

| Points | Base angle | z | Distances | Contributes to averages |
| --- | --- | --- | --- | --- |
| z=0 left | `-30°` | `0 mm` | `0`, `60`, `120 mm` | Yes |
| z=0 center | `0°` | `0 mm` | `0`, `60`, `120 mm` | Yes |
| z=0 right | `30°` | `0 mm` | `0`, `60`, `120 mm` | Yes |
| z=50 center | `0°` | `50 mm` | `30`, `75`, `120 mm` | No; validation only |
| z=25 center | `0°` | `25 mm` | `15`, `60`, `120 mm` | No; validation only |

If a grab succeeds, the point is marked complete and the workflow advances. If no stop input is detected, `RUN_POINT` still returns outer `status:"completed"`, but the nested phase is `needs_adjustment`. Transport/positioning/session errors return outer `status:"failed"` and nested `phase:"failed"`.

At final completion, the firmware saves:

- `rot_off_deg`: average rotation nudge from the nine z=0 contributor points. Added to future normal absolute base-angle targets.
- `ik_off_mm`: average distance nudge from the same nine points. Added to future normal IK distances.
- `st_map`: JSON-encoded string containing all 15 point results and session metadata. It is diagnostic/history data; runtime motion uses the two averages, not per-point lookup.

The nested `stencil_calibration` result includes:

- Session: `sessionId`, `phase`, `active`, `pointIndex`, total/offset/validation counts.
- Base movement: `homeDirection`, `baseMoveSpeed`, `baseMoveSkipped`, `lastBaseTargetAngleDegrees`.
- Current nominal point: `pointId`, `baseAngleDegrees`, `baseDistanceMm`, `zHeightMm`, `offsetContributor`.
- Adjusted target: `targetAngleDegrees`, `targetDistanceMm`, `targetZHeightMm`, current nudge totals, and attempts.
- Result/status: `grabbed`, `message`, optional `error`, `savedRotationOffsetDegrees`, `savedIkOffsetMm`.
- `points`: all 15 point definitions and their current completion/grab/nudge/attempt state.

Session state is not persisted and is lost on reboot. Only the three final stencil keys survive reboot.

## 6. Camera Actions and Binary Photo Protocol

The following exact, case-sensitive actions share one firmware path:

- `photo`
- `detect_object`
- `detect_color`
- `calibrate_depth`

Example:

```json
{
  "sender": "ai_server",
  "action_id": "photo-1",
  "action": "detect_object",
  "phrase": ["red cup", "green bottle"],
  "use_model": true
}
```

The ESP32 does not run object, color, or depth ML. These action names change correlation metadata for external consumers; the firmware itself captures and publishes a fresh JPEG.

The camera tries SVGA (`800x600`), VGA (`640x480`), then QVGA (`320x240`), with JPEG quality `20`, one framebuffer, and PSRAM when available. It flushes one frame and retries capture once after camera reinitialization.

### Photo response sequence

1. Normal `in_progress`, with `type` equal to the requested photo action.
2. One binary-framed photo message on `{mqtt_user}/test`.
3. Another `in_progress` with the same `type` and `log:"sent"`.

There is no firmware `completed` photo response. The second `in_progress` is emitted even if capture or publishing failed, so it is not proof that a valid JPEG arrived.

### Binary framing

The photo message is deliberately not valid JSON and is not base64 encoded. It is:

```text
<JSON object with final brace removed>,"payload":<raw JPEG bytes>}
```

Metadata before `payload` can contain:

| Field | Behavior |
| --- | --- |
| `sender:"firmware"` | Constant |
| `action_id` | Included when nonempty |
| `photo:"sending_photo"` | Constant marker |
| `requested_by` | Incoming sender when nonempty |
| `phrase` | Copied when present |
| `use_model` | Copied from either accepted spelling when present |
| workflow fields | Copied when active |

A decoder must locate the byte marker `,"payload":`, parse only the prefix as JSON after restoring `}`, and treat the bytes before the final envelope `}` as JPEG. Validate JPEG SOI `FF D8` and EOI `FF D9`. Do not pass the complete MQTT payload to a normal JSON parser.

`calibrate_depth` may be consumed by an external visual-AI service, but any later `sender:"visual_ai"` result is not generated or interpreted as a command result by this ESP32 API.

### External reach-and-grab orchestration

When the configured Vision server has automatic reach-and-grab enabled, a `detect_object` photo request can start a larger shared-topic workflow. The initiating client sends a nonempty scalar `phrase` and boolean `use_model`; it may also send `model_name`, `box_threshold`, `text_threshold`, `MagnetPosition`, and workflow IDs. Client senders must not impersonate `ai_server`, `visual_ai`, or `firmware`, and `use_model:"train"` is not supported.

After the ESP32 publishes the photo, the external Vision server may publish progress with the original action ID, generate sequential `baseRotate`, `controlik`, `gripper`, and `calibrationvalues` child commands using its own `rg-...` action IDs, and finally publish one terminal result:

```json
{
  "sender": "visual_ai",
  "action_id": "original-detect-action-id",
  "status": "completed",
  "type": "detect_object",
  "stage": "reach_and_grab_completed",
  "grab_status": "completed"
}
```

This orchestration is not implemented by the ESP32. A GUI should monitor it rather than replaying the child commands. The overall operation is terminal only when `sender` is `visual_ai`, the original action ID matches, and `status` is `completed` or `failed`. `stage:"detection_only"` means detection succeeded without robot motion. A timeout must not trigger an automatic retry because the robot may have moved even if the terminal result was lost.

For each `rg-...` child command, firmware `status:"in_progress"` confirms that the command entered dispatch. The orchestrator must wait for the exact same child `action_id` with firmware `status:"completed"` before publishing the next child. Because this firmware uses a single receive slot and synchronous handlers, commands must not overlap.

## 7. OTA Action

### `ota_update`

```json
{
  "sender": "release_service",
  "action_id": "ota-1",
  "action": "ota_update",
  "url": "https://example.com/releases/download/v1.2.3/firmware.bin",
  "version": "v1.2.3",
  "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

| Field | Requirement | Behavior |
| --- | --- | --- |
| `url` | Required nonempty string | HTTPS firmware binary URL |
| `version` | Optional string/number | Desired version; if absent, extracted from `/download/{version}/` in the URL |
| `sha256` | Optional string | 64 hexadecimal characters, optionally prefixed by `sha256:` |

There is no `token` or authorization-header support in the firmware. The download client also uses insecure TLS certificate handling.

An invalid nonempty SHA-256 string produces an OTA debug warning but does not reject the update; verification is simply skipped. A valid SHA is compared after streaming and aborts on mismatch.

The firmware persists desired version, URL, SHA, last attempted version, OTA state, and last error in the `ota` namespace. States include `idle`, `outdated`, `downloading`, `flashing`, `rebooting`, `booted`, and `failed`.

Failures return outer `status:"failed"` with `type:"ota_update"`; detailed failure text is available through OTA debug messages and later ready/heartbeat telemetry, not in the terminal command envelope. Success sets `rebooting` and calls `ESP.restart()`, so a success `completed` response normally never publishes. Automatic desired-version enforcement is disabled; updates occur only through this action.

## 8. Unsolicited Outbound Messages

### 8.1 Ready

Published once after each successful MQTT connection to `{mqtt_user}/test`:

| Field | Meaning |
| --- | --- |
| `sender:"firmware"`, `status:"ready"` | Message discriminator |
| `message` | OTA-aware ready text |
| `firmware_version`, `compiled_firmware_version`, `running_version` | Version telemetry |
| `desired_version`, `ota_state`, `ota_update_required`, `last_attempted_version` | OTA state |
| `last_error`, `desired_url` | Included only when nonempty |
| `ready_message_revision` | Currently `20` |
| `last_reset_reason` | ESP reset-reason text |
| `boot_free_heap`, `boot_min_free_heap`, `current_free_heap` | Heap diagnostics |

The ESP32 requests the status-topic subscription and sends an initial heartbeat before the ready message, so consumers must not assume ready is the first post-connect publish. PubSubClient's local `subscribe()` return and the broker SUBACK are not exposed in ready telemetry.

### 8.2 Heartbeat

Published to `{mqtt_user}/HEARTBEAT` while heartbeat is enabled, nominally every `4000 ms` while the listen loop is idle:

```json
{
  "sender": "firmware",
  "log": "heartbeat",
  "time": "YYYY-MM-DD HH:MM:SS",
  "firmware_version": "...",
  "desired_version": "...",
  "ota_state": "...",
  "ota_update_required": false,
  "ELBOW_ANGLE": 120.0,
  "WRIST_ANGLE": 90.0,
  "TWIST_ANGLE": 90.0,
  "GRIPPER_ANGLE": 180.0
}
```

Timestamps are generated after `configTime(0,0,...)` and therefore use UTC-style system time, despite having no timezone suffix.

The code can optionally publish three additional hover snapshots to the status topic when `Heartbeat::send(false)` is called. Normal MQTT paths call `send(true)`, so those snapshot messages are not normally emitted.

### 8.3 Count

When heartbeat is enabled, the main MQTT maintenance path may publish this to `{mqtt_user}/test` after its three-second gate:

```json
{"count":1,"time":"YYYY-MM-DD HH:MM:SS"}
```

Because the listen loop blocks waiting for commands, count is not a reliable periodic liveness signal. Use the heartbeat topic for liveness. Count messages have no `sender` and are explicitly ignored by the firmware's own callback.

### 8.4 Debug

Gripper and OTA code can publish to `{mqtt_user}/test`:

```json
{"sender":"firmware","debug":"gripper","msg":"handleGrab: starting GRAB"}
```

Debug messages have no `action_id`, status, phrase, or workflow context. They are diagnostic side traffic and must not be treated as command completion. The firmware sender marker ensures the ESP32 rejects its own debug echo before dispatch.

## 9. Persistence and Runtime Defaults

| Namespace | MQTT-related data |
| --- | --- |
| `mqtt` | Broker host/port, username, password, client ID |
| `config` | Current joint angles, hover points, perch values, stencil map/offsets, firmware compatibility version |
| `rot` | Base counts, calibration/profile flags, trusted position, neutral and speed profile |
| `ota` | Running/desired versions, URL/SHA, state, last attempt, last error |

Operational defaults and optionality:

- Arm servos restore saved angles or default to `90°` at startup.
- Gripper restores its saved angle or defaults to `180°` when first initialized.
- Perch has usable defaults and is optional.
- z=0 IK has built-in fallback geometry and can operate without saved hover calibration, although accuracy and the table guard differ.
- Nonzero-z IK requires valid z=0 and z=50 point sets.
- Base firmware-step movement can use estimated counts, but degree/absolute precision controls require calibration as described above.
- Stencil keys are optional for basic motion. Missing rotation/reach offsets mean zero corrections; the map is not used to block motion.

Holding the BOOT button for three seconds performs a connection reset outside MQTT: it clears only the `wifi` and `mqtt` namespaces, then reboots into configuration mode. Robot calibration in `config` and `rot`, along with OTA state in `ota`, is preserved so the robot can change networks without being recalibrated.

## 10. Failure and No-Response Matrix

| Situation | MQTT-observable result |
| --- | --- |
| Raw payload begins with `{"count":` | Ignored |
| Incoming `sender:"firmware"`, including large detail/photo payloads | Ignored before command allocation/dispatch |
| Valid JSON without `action` | Ignored as shared-topic side traffic |
| Command is too large for the `512`-byte dispatch document | Dispatcher JSON parse error; no MQTT response |
| Dispatcher cannot parse JSON | No response |
| Missing or unknown/case-mismatched `action` | No response |
| Known action with ID | Initial `in_progress`, then action-specific behavior |
| Known action without ID | No initial response; handler-dependent empty-ID terminal traffic may occur |
| Handler validation or motion failure | Usually `failed`; detailed base/stencil responses include nested `error` |
| `calibrate` parse failure | Initial `in_progress` may already have published, then no terminal response |
| Perch move blocked | Still reports `completed` |
| Stencil grab misses | Reports `completed` with `phase:"needs_adjustment"` |
| Photo WiFi/camera/capture/publish failure | No failed terminal response; controller still emits `in_progress` with `log:"sent"` |
| OTA fails | `failed`; reason primarily in debug and OTA telemetry |
| OTA succeeds | Reboots, usually before terminal `completed` |
| Detailed-result MQTT publish fails | Attempts a smaller fallback `failed` response |

## 11. Source Cross-Check Checklist

When firmware MQTT behavior changes, audit at least these sources and update this contract in the same change:

```bash
rg 'strcmp\(act|sendInProgress|sendCompleted|sendCompletedDetails' firmware
rg 'controlType|calibration_type|command"\]|servoName|action_id.*live' firmware
rg 'STATUS_TOPIC|HEARTBEAT_TOPIC|publishStatusPhoto|sendDebug' firmware
rg 'Preferences|putFloat|putString|remove\(' firmware/Action*.cpp firmware/BuddyMQTT.cpp
```
