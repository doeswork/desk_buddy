# ESP32 MQTT Capability Inventory

This file inventories what the firmware currently does over MQTT. It is a code-facing contract, not a user walkthrough. For copy-paste command examples, use `README.md`.

## Topics and Message Flow

| Direction | Topic | Source |
| --- | --- | --- |
| Subscribe | `{mqtt_user}/test` | Built from MQTT preference `user` |
| Publish command/status/debug/photo/ready/count | `{mqtt_user}/test` | Built from MQTT preference `user` |
| Publish heartbeat telemetry | `{mqtt_user}/HEARTBEAT` | Built from MQTT preference `user` |

General message behavior:

- MQTT settings are loaded from the `mqtt` preferences namespace: `server`, `port`, `user`, `password`, `client_id`.
- Incoming messages whose raw payload starts with `{"count":` are ignored by the MQTT callback.
- Incoming messages with `sender:"firmware"` are ignored by the MQTT callback and dispatcher.
- Invalid JSON at dispatch time produces serial output only; no MQTT failure response is published.
- Missing `action` produces serial output only; no MQTT failure response is published.
- Unknown `action` produces serial output only; no MQTT failure response is published.
- Known actions with an `action_id` receive an initial `in_progress` response before dispatch.
- If incoming `workflow_id` is a JSON long, outgoing messages in that dispatch include `workflow_id`; if `workflow_event_id` is a long, it is included too.
- `phrase` is copied through for supported response paths.
- `use_model` or `useModel` is copied into `in_progress` responses and photo publish envelopes when present.

## Outbound Envelopes

### Ready Message

Published once after each successful MQTT connection to `{mqtt_user}/test`.

Fields:

| Field | Notes |
| --- | --- |
| `sender:"firmware"` | Constant |
| `status:"ready"` | Constant |
| `message` | OTA-aware readiness text |
| `firmware_version`, `compiled_firmware_version`, `running_version` | Firmware version telemetry |
| `desired_version`, `ota_state`, `ota_update_required`, `last_attempted_version`, `last_error`, `desired_url` | OTA telemetry |
| `ready_message_revision` | Currently `20` |
| `last_reset_reason`, `boot_free_heap`, `boot_min_free_heap`, `current_free_heap` | Reset/heap diagnostics |

### Command Responses

| Envelope | Fields |
| --- | --- |
| `in_progress` | `sender`, `action_id`, `status:"in_progress"`, optional `type`, optional `log`, optional `phrase`, optional `use_model`, optional workflow fields |
| `completed` | `sender`, `action_id`, `status:"completed"`, optional `type`, optional `phrase`, optional workflow fields |
| `failed` | Same as completed, with `status:"failed"` |
| Detailed result | Same as completed/failed plus one nested result key such as `base_rotation`, `stencil_calibration`, `calibrationvalues`, or a calibration subtype key |

Detailed result keys emitted by code:

| Key | Produced by |
| --- | --- |
| `base_rotation` | `baseRotate`, `calibrate` with `base_rotation_profile` |
| `stencil_calibration` | `stencilCalibrate` |
| `calibrationvalues` | `calibrationvalues` |
| `hover_over_min`, `hover_over_mid`, `hover_over_max` | `calibrate` hover snapshots |
| `hover_min_120`, `hover_mid_120`, `hover_max_120` | `calibrate` z=50 trapezoid top-edge snapshots; legacy names retained |
| `PERCH_ELBOW_ANGLE`, `PERCH_WRIST_ANGLE`, `PERCH_TWIST_ANGLE`, `PERCH_MIN`, `PERCH_MID`, `PERCH_MAX` | `calibrate` perch writes |

### Photo Envelope

Published to `{mqtt_user}/test` by `photo`, `detect_object`, `detect_color`, and `calibrate_depth`.

Fields:

| Field | Notes |
| --- | --- |
| `sender:"firmware"` | Constant |
| `action_id` | Included when present |
| `photo:"sending_photo"` | Constant |
| `requested_by` | Incoming `sender`, when present |
| `phrase` | Copied when present |
| `use_model` | Copied when present |
| workflow fields | Copied when present |
| `payload` | Raw JPEG bytes streamed into the JSON envelope |

Photo nuance: after dispatching the photo action, the controller sends a second `in_progress` message with `log:"sent"` rather than a final `completed`.

### Debug Envelope

Published to `{mqtt_user}/test` by gripper and OTA helper paths.

| Field | Notes |
| --- | --- |
| `debug` | Component label, e.g. `gripper` or `OTA` |
| `msg` | Debug text |

### Heartbeat and Status Count

| Message | Topic | Fields |
| --- | --- | --- |
| Main heartbeat | `{mqtt_user}/HEARTBEAT` | `sender:"firmware"`, `log:"heartbeat"`, `time`, firmware/OTA fields, `ELBOW_ANGLE`, `WRIST_ANGLE`, `TWIST_ANGLE`, `GRIPPER_ANGLE` |
| Status count | `{mqtt_user}/test` | `count`, `time` |
| Hover snapshots | `{mqtt_user}/test` | Only when `Heartbeat::send(false)` is used; current MQTT loop calls `send(true)`, so these are normally not emitted |

## Inbound Actions

Top-level `action` values recognized by `ActionController`:

| `action` | Handler | Completion response |
| --- | --- | --- |
| `gripper` | `ActionGripper` | `completed` or `failed` |
| `baseRotate` | `ActionBaseRotate` | Detailed `base_rotation` |
| `servo` | `ActionServo` | `completed` or `failed` |
| `controlik` | `ActionInverseKinematics` | `completed` or `failed` |
| `stencilCalibrate` | `ActionStencilCalibrate` | Detailed `stencil_calibration` |
| `perch` | `ActionPerch` | `completed` |
| `calibrate` | `ActionCalibrate` | Calibration-specific completed/failed response |
| `calibrationvalues` | `BuddyMQTT::sendCalibrationValues` | Detailed `calibrationvalues` |
| `photo` | `ActionPhoto` | Photo publish plus `in_progress` `log:"sent"` |
| `detect_object` | `ActionPhoto` | Photo publish plus `in_progress` `log:"sent"` |
| `detect_color` | `ActionPhoto` | Photo publish plus `in_progress` `log:"sent"` |
| `calibrate_depth` | `ActionPhoto` | Photo publish plus `in_progress` `log:"sent"` |
| `ota_update` | `ActionOTA` | `completed`/`failed` unless successful reboot prevents final send |

### `gripper`

Accepted command forms:

| Field/Value | ESP32 action |
| --- | --- |
| `command:"GRAB"` | Opens to `180`, closes toward `0`, succeeds on stop button press, saves `GRIPPER_ANGLE` |
| `command:"DROP"` | Opens to `180`, saves `GRIPPER_ANGLE` |
| `command:"SOFTHOLD"` | Releases while stop button remains pressed, saves `GRIPPER_ANGLE` |
| `position:0..180`, optional `speed` | Moves gripper servo to position, saves `GRIPPER_ANGLE` |

Also emits debug messages during gripper initialization and movement.

### `servo`

Accepted fields:

| Field | Values |
| --- | --- |
| `servoName` | `ELBOW`, `WRIST`, `TWIST` |
| `position` | `0..180` |
| `action_id:"live"` | Bypasses pose/table safety guard and uses live slow movement |

Normal servo moves update `ELBOW_ANGLE`, `WRIST_ANGLE`, or `TWIST_ANGLE` preferences. Normal elbow/wrist moves may be blocked by the table safety guard derived from hover calibration and perch/current pose.

### `baseRotate`

Accepted `controlType` values:

| `controlType` | Required/optional fields | ESP32 action |
| --- | --- | --- |
| `STATUS` | none | Updates encoder tracking, corrects true north if pressed, returns status |
| `HOME` | `direction` | Rotates until true-north switch is detected |
| `CALIBRATE` | `direction`, optional `speed` | Measures one direction counts-per-rev |
| `CALIBRATE_PROFILE` | optional `neutralServoAngle` | Full left/right profile calibration and neutral balancing |
| `CALIBRATE_BOTH` | optional `neutralServoAngle` | Alias for profile calibration |
| `ENCODER` | `direction`, `value`, optional `speed` | Moves firmware steps; uses calibrated or estimated counts |
| `STEPS` | `direction`, `value` or `steps`, optional `speed` | Alias-style step movement |
| `DEGREES` | `direction`, `value`, optional `speed` | Relative degree movement, requires calibration |
| `ANGLE` | `value`, optional `speed` | Absolute angle target, requires trusted calibrated position, applies `rot_off_deg` |

Accepted `direction`: `LEFT`, `RIGHT`.

Accepted `speed`: `veryslow`, `slow`, `regular`, `fast`, `superfast`.

`base_rotation` status object includes calibration flags, trusted position, current angle/counts, `encoderSign`, gear math, counts-per-step, estimated-count flags, neutral/profile details, true-north diagnostics, raw encoder value, `rotationOffsetDegrees`, speed profile angles, and optional `error`.

### `controlik`

Accepted fields:

| Field | Notes |
| --- | --- |
| `distance` | Required float, must be `>= 0` |
| `z_height` | Optional float, default `0`, must be from `0` through `50`; current common values are `0`, `25`, and `50` |

Uses saved `hover_over_min/mid/max` interpolation at `z_height:0` when valid, otherwise direct fallback math. For nonzero `z_height`, `controlik` uses a trapezoid workspace between the z=0 calibration edge and the z=50 calibration edge stored in the legacy-named `hover_*_120` keys. Requests outside that trapezoid return `failed`; for example, if `hover_over_min.DISTANCE` is `0` and `hover_min_120.DISTANCE` is `30`, then `distance:0,z_height:50` is outside the workspace, while `distance:15,z_height:25` is on the slanted boundary. Applies saved `ik_off_mm` unless called internally with offset disabled.

The `hover_*_120` names are retained for storage/MQTT compatibility but now represent the reachable top edge at z=50mm, not a rectangular full-width plane.

### `calibrate`

Accepted `calibration_type` values:

| `calibration_type` | Required fields | Stored data |
| --- | --- | --- |
| `hover_over_min` | `distance` | Current or supplied `ELBOW`, `WRIST`, `TWIST`, plus `DISTANCE` |
| `hover_over_mid` | `distance` | Same |
| `hover_over_max` | `distance` | Same |
| `hover_min_120` | `distance` | Required for nonzero-z IK; first reachable z=50 point, often around 30mm |
| `hover_mid_120` | `distance` | Required for nonzero-z IK; middle reachable z=50 point, for example around 75mm |
| `hover_max_120` | `distance` | Required for nonzero-z IK; max reachable z=50 point, for example around 120mm |
| `perch_elbow_angle` | `value` or `distance` | `p_elbow` |
| `perch_wrist_angle` | `value` or `distance` | `p_wrist` |
| `perch_twist_angle` | `value` or `distance` | `p_twist` |
| `perch_min` | `value` or `distance` | `p_min` |
| `perch_mid` | `value` or `distance` | `p_mid` |
| `perch_max` | `value` or `distance` | `p_max` |
| `base_rotation_profile` | optional `neutralServoAngle` | Runs base profile calibration |

If `calibration_type` is missing, firmware defaults to `hover_over_min`. Unknown calibration types publish `failed`.

### `calibrationvalues`

Returns saved calibration and effective default inventory under `calibrationvalues`.

Included groups:

- Servo/gripper angle prefs: `ELBOW_ANGLE`, `WRIST_ANGLE`, `TWIST_ANGLE`, `GRIPPER_ANGLE`.
- Perch values: `PERCH_ELBOW_ANGLE`, `PERCH_WRIST_ANGLE`, `PERCH_TWIST_ANGLE`, `PERCH_MIN`, `PERCH_MID`, `PERCH_MAX`.
- Perch readiness/effective values: `perch_configured`, `perch_distance_configured`, `perch_defaults_applied`, `perch_effective`.
- Hover objects: `hover_over_min`, `hover_over_mid`, `hover_over_max`, `hover_min_120`, `hover_mid_120`, `hover_max_120`.
- IK readiness/source fields: `ik_hover_calibrated`, `ik_z120_calibrated`, `ik_z50_calibrated`, `ik_hover_source`, `ik_z120_source`, `ik_z50_source`.
- Stencil fields: `rot_off_deg`, `ik_off_mm`, `st_map`, `stencil_calibrated`, `stencil_runtime_mode`.
- Base rotation fields: `base_rotation_calibrated`, `base_rotation_profileCalibrated`, `base_rotation_leftCountsPerRev`, `base_rotation_rightCountsPerRev`, `base_rotation_lastCounts`, `base_rotation_lastValid`, `base_rotation_ready`.
- Combined readiness: `motion_calibration_ready`, `initial_calibration_ready`.

### `stencilCalibrate`

Accepted `command` values:

| `command` | Fields | ESP32 action |
| --- | --- | --- |
| `START` | none | Moves arm to perch, homes base to true north by rotating `RIGHT` at `veryslow`, then starts the 15-point stencil session if absolute base angle is ready |
| `RUN_POINT` | none | Opens gripper, perches before required lane rotations, moves base only if the stencil target angle changed, moves IK with offsets disabled, attempts grab |
| `ADJUST` | optional `rotationNudgeDegrees`, optional `distanceNudgeMm` | Adds nudges to current point and prompts again |
| `ADJUST_PREVIOUS` | optional `rotationNudgeDegrees`, optional `distanceNudgeMm` | Adds nudges to the most recently completed point, returns to it, and immediately retries it |
| `STATUS` | none | Returns current stencil session status |
| `CANCEL` | none | Stops active session without saving active-session changes |
| `CLEAR` | none | Removes saved `st_map`, `rot_off_deg`, `ik_off_mm` |

Session points run in this order:

| Point group | Angle | z height | Distances |
| --- | --- | --- | --- |
| z=0 left offset points | `-30` degrees | `0` mm | `0`, `60`, `120` mm |
| z=0 center offset points | `0` degrees | `0` mm | `0`, `60`, `120` mm |
| z=0 right offset points | `30` degrees | `0` mm | `0`, `60`, `120` mm |
| z=50 center validation points | `0` degrees | `50` mm | `30`, `75`, `120` mm |
| z=25 center validation points | `0` degrees | `25` mm | `15`, `60`, `120` mm |

Stencil base movement uses `veryslow`. The arm moves to the saved perch pose before true-north homing and before each required base lane rotation, giving the operator room to place the next peg. Repeated same-lane points do not re-command base rotation unless `rotationNudgeDegrees` changes the target angle. IK and gripper still run every time `RUN_POINT` is sent.

Use `ADJUST_PREVIOUS` when a point grabbed successfully but the operator decides the just-run target needs a correction. The firmware applies the nudges to `currentPointIndex - 1`, marks that point incomplete, retries it immediately, and advances back to the next point only if the retry succeeds. Use normal `ADJUST` for the current point after a failed grab.

On completion, firmware saves per-point diagnostics in `st_map`, including each point `id`, angle, distance, z height, nudges, attempts, grab result, and whether the point contributed to offsets. The saved runtime offsets `rot_off_deg` and `ik_off_mm` are averaged from the 9 z=0 offset-contributor points only. z=25 and z=50 points are validation diagnostics; they must pass to complete the session but do not contribute to runtime offsets. Runtime correction uses average offsets, not the full point map.

The `stencil_calibration` status object includes `phase`, `active`, `pointIndex`, `totalPointCount`, `offsetPointCount`, `validationPointCount`, `homeDirection`, `baseMoveSpeed`, `baseMoveSkipped`, `lastBaseTargetAngleDegrees`, current point id/angle/distance/z height, target angle/distance/z height after nudges, current nudges, attempts, grabbed state, saved offsets, message/error, and a `points` progress array.

### `perch`

Moves wrist, elbow, and twist to saved perch values. Defaults are used when no perch prefs exist:

| Field | Default |
| --- | --- |
| elbow | `120` |
| wrist | `90` |
| twist | `90` |
| min/mid/max distances | `0`, `50`, `100` |

The `perch` action publishes `completed` without checking individual move return values.

### `photo`, `detect_object`, `detect_color`, `calibrate_depth`

All four actions use the same photo path:

- Parse sender/action/action_id/phrase/use_model.
- Require WiFi connection.
- Initialize camera if needed, trying SVGA, VGA, then QVGA at JPEG quality `20`.
- Flush one frame buffer, capture a fresh JPEG, try one camera reinitialization on capture failure.
- Publish JPEG through the photo envelope.
- Return the frame buffer.

The firmware does not perform object/color ML locally in this path; it captures and publishes a photo for external handling.

### `ota_update`

Accepted fields:

| Field | Notes |
| --- | --- |
| `url` | Required firmware binary URL |
| `sha256` | Optional; accepts 64 hex chars or `sha256:` prefix |
| `version` | Optional; otherwise extracted from `/download/{version}/` in URL |

Side effects:

- Saves desired OTA target and last attempted version in the `ota` namespace.
- Downloads over HTTPS with insecure TLS client.
- Streams to flash with optional SHA256 verification.
- Updates OTA state: `downloading`, `flashing`, `rebooting`, `failed`, then reboot/boot states.
- Publishes OTA debug messages.
- On success, calls `ESP.restart()`, so the final controller `completed` may not publish.

## ESP32 Side Effects Inventory

| Area | Side effects |
| --- | --- |
| MQTT connection | Loads MQTT prefs, subscribes to status topic, sends ready message, marks OTA booted |
| WiFi | Connects from saved `wifi` prefs, starts config web server when missing/failed credentials or BOOT hold requests WiFi config |
| Factory reset | BOOT held at startup or during MQTT reconnect clears `wifi`, `mqtt`, `config`, and `rot` prefs, then reboots |
| Servo arm | Initializes servos on pins 47, 39, 19; restores saved arm angles; moves with easing; table guard can block elbow/wrist moves |
| Gripper | Initializes servo pin 20 and stop pins 45/46; saves `GRIPPER_ANGLE`; emits debug MQTT |
| Base rotation | Uses servo pin 21, AS5600 analog pin 1, true-north pin 14; saves rotation profile/position in `rot` namespace |
| IK | Loads hover calibration and `ik_off_mm`; updates elbow/wrist through `ArmServos` |
| Stencil | Saves `st_map`, `rot_off_deg`, `ik_off_mm`; disables stored offsets during calibration attempts |
| Camera | Initializes ESP camera, captures JPEG, publishes payload, recovers once on capture failure |
| OTA | Saves OTA state/targets in `ota` and version in `config`; may reboot |
| LED | Indicates WiFi/MQTT/OTA/factory-reset states |

## No-Response and Failure Inventory

| Case | MQTT response |
| --- | --- |
| Incoming payload starts with `{"count":` | Ignored |
| Incoming JSON has `sender:"firmware"` | Ignored |
| Dispatcher JSON parse failure | No MQTT response |
| Missing `action` | No MQTT response |
| Unknown `action` | No MQTT response |
| Known action without `action_id` | No initial `in_progress`; completion may publish with empty `action_id` depending on handler |
| Command validation failure inside most handlers | Publishes `failed` through controller or handler |
| `calibrate` JSON parse failure | Serial output only; no MQTT response |
| Photo camera/WiFi/capture failure | Usually no final failed response; controller still sends `in_progress` with `log:"sent"` after `ActionPhoto::run` returns |
| `sendCompletedDetails` publish failure | Attempts fallback `failed` response when `action_id` is present |

## Code Cross-Check

Use these searches when changing MQTT behavior:

```bash
rg 'strcmp\(act|strcasecmp\(ctl|calibration_type|sendCompletedDetails|publishStatusPhoto|sendDebug'
rg 'command"\]|servoName|controlType|action_id == "live"|photo"'
rg 'STATUS_TOPIC|HEARTBEAT_TOPIC|sender"\]|workflow_id|use_model|useModel'
```
