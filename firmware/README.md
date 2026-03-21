# Buddy ESP32 MQTT Message Guide

The firmware listens for JSON commands on the status topic (see below), replies with an `in_progress` message, then finishes with `completed` (or `completed` with data). Every command should include a unique `action_id` so you can correlate responses. The firmware ignores any message whose `sender` is `"firmware"`.

- Command topic: `esp32_5/test`
- Heartbeat topic: `esp32_5/HEARTBEAT`
- Typical fields in replies: `sender:"firmware"`, `action_id`, `status:"in_progress"| "completed"| "failed"`, optional `type`, optional data object.

---
## Base Rotate — `action: "baseRotate"`
Control the base using magnets, encoder counts, or by going to a stored origin magnet. Replies include an `origin_magnet` object in the completed message.

Rotate by magnets
```json
{"action":"baseRotate","action_id":"69","controlType":"MAGNET","direction":"RIGHT","speed":"slow","value":3,"sender":"ai_server"}
```

Rotate by encoder counts
```json
{"action":"baseRotate","action_id":"70","controlType":"ENCODER","direction":"LEFT","speed":"fast","value":800,"sender":"ai_server"}
```

Go to a specific origin magnet (1–12)
```json
{"action":"baseRotate","action_id":"71","controlType":"originmagnet","direction":"RIGHT","speed":"slow","value":4,"sender":"ai_server"}
```
Fields:
- `controlType`: `"MAGNET" | "ENCODER" | "originmagnet"`
- `direction`: `"LEFT" | "RIGHT"` (used for motion and tie‑breaks)
- `speed`: `"veryslow" | "slow" | "regular" | "fast" | "superfast"`
- `value`: magnet count, encoder ticks, or target magnet

---
## Origin Magnet Helper — no `action` required
Read or set the stored origin magnet without moving the base.
```json
{"action_id":"originmagnet","origin_magnet":5,"sender":"desk_buddy_web"}
```
- `origin_magnet` or `value`: magnet index 1–12
- Replies with `completed` and an `origin_magnet` object.

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
Move arm based on distance (interpolates using saved hover calibrations if available).
```json
{"action":"controlik","action_id":"30","distance":85.0,"z_height":0.0,"sender":"ai_server"}
```
- `distance`: float (required)
- `z_height`: float mm (optional, default 0; requires hover_*_120 calibrations for non-zero behavior)

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

Hover snapshots at z=120mm (used by `controlik` when `z_height > 0`)
```json
{"action":"calibrate","action_id":"50b","calibration_type":"hover_min_120","distance":20,"sender":"ai_server"}
{"action":"calibrate","action_id":"51b","calibration_type":"hover_mid_120","distance":60,"sender":"ai_server"}
{"action":"calibrate","action_id":"52b","calibration_type":"hover_max_120","distance":110,"sender":"ai_server"}
```
- Same idea as hover_over_* but calibrated with the gripper ~120mm above the table.

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

---
## Calibration Values Dump — `action: "calibrationvalues"`
Returns all stored calibration-related prefs as a `calibrationvalues` object. Missing keys are reported as `null`. If publish fails you’ll receive a `status:"failed"`.
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
    "origin_magnet":5
  }
}
```

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
- `type` in replies mirrors the action or calibration subtype (e.g., `perch_elbow_angle`, `calibrationvalues`, `detect_object`). Data objects appear under their own keys (`origin_magnet`, `calibrationvalues`, etc.).

Use these templates directly or adapt them to your tooling to drive the ESP32 over MQTT. Ensure every command sets a unique `action_id` so you can pair requests and replies.
