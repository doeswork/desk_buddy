# First-Time Robot Calibration Walkthrough

This is the practical first-time calibration path for a Desk Buddy robot over MQTT, without the Rails app. Use `/home/jeffy4080/robots/desk_buddy/manually_calibrate_robot.py` for the commands below.

For the exhaustive MQTT inventory, see `MQTT_SPEC.md`.

## Before You Start

Safety and hardware checks:

- ESP32 WiFi/MQTT config is already saved.
- Servo power can handle the arm and base without browning out the ESP32.
- Arm, gripper, and base have clear space to move.
- Gripper is open and empty.
- True-north switch/bump is installed.
- AS5600 encoder value changes when the base rotates.
- Base profile calibration can rotate multiple full turns.

MQTT topics:

| Topic | Use |
| --- | --- |
| `{mqtt_user}/test` | Commands, replies, status, debug, and photo messages |
| `{mqtt_user}/HEARTBEAT` | Heartbeat telemetry |

Command rules:

- Use a unique `action_id` for every direct MQTT command.
- Do not publish commands with `sender:"firmware"`.
- Ignore unrelated `count`, `heartbeat`, `debug`, and non-matching `action_id` messages.
- Most commands publish `in_progress`, then `completed` or `failed`.

## Helper Setup

Install and configure:

```bash
python3 -m pip install paho-mqtt
export DESK_BUDDY_MQTT_BROKER="mqtt.deskbuddy.ai"
export DESK_BUDDY_MQTT_ADMIN_USER="YOUR_ADMIN_MQTT_USERNAME"
export DESK_BUDDY_MQTT_ADMIN_PASSWORD="YOUR_ADMIN_MQTT_PASSWORD"
export DESK_BUDDY_MQTT_COMMAND_TOPIC="esp32_5/test"
export DESK_BUDDY_MQTT_HEARTBEAT_TOPIC="esp32_5/HEARTBEAT"
export DESK_BUDDY_MQTT_CA_CERT="./mqtt-ca.crt"
cd /home/jeffy4080/robots/desk_buddy
python3 manually_calibrate_robot.py --help
```

`DESK_BUDDY_MQTT_ADMIN_USER` and `DESK_BUDDY_MQTT_ADMIN_PASSWORD` authenticate to Mosquitto. `DESK_BUDDY_MQTT_COMMAND_TOPIC` is the ESP32 robot command topic.

The helper wraps reusable functions such as `send_command()`, `check_calibration_values()`, `open_gripper()`, `calibrate_base_profile()`, `move_servo()`, `save_hover_point()`, `test_ik()`, `save_z_height_point()`, and `stencil_*()`.

## Raw MQTT Pattern

Prefer the helper while calibrating. If you publish JSON yourself, keep the same firmware action names and fields. Extra Rails changes are not required.

```json
{"sender":"manual_calibrator","action_id":"check_cal_001","action":"calibrationvalues"}
```

Useful readiness fields in the final `calibrationvalues` reply:

- `base_rotation_ready`
- `ik_hover_calibrated`
- `ik_z50_calibrated`
- `ik_z120_calibrated` may also appear for compatibility
- `stencil_calibrated`
- `motion_calibration_ready`
- `initial_calibration_ready`

## Calibration Flow

Run these in order. Stop and fix failures before moving to later steps.

| Step | CLI command | MQTT action / key | Success check |
| --- | --- | --- | --- |
| 1. Inspect state | `python3 manually_calibrate_robot.py check` | `calibrationvalues` | Matching `completed`; saved/default values visible |
| 2. Open gripper | `python3 manually_calibrate_robot.py open-gripper` | `gripper` `DROP` | Gripper opens to `180`; `completed` |
| 3. Base profile | `python3 manually_calibrate_robot.py base-profile` | `baseRotate` `CALIBRATE_PROFILE` | `base_rotation.calibrated:true`; counts per rev > `0` |
| 4. Base status | `python3 manually_calibrate_robot.py base-status` | `baseRotate` `STATUS` | `positionTrusted:true` when calibrated |
| 4. Angle check | `python3 manually_calibrate_robot.py base-angle 0` | `baseRotate` `ANGLE` | Base moves to absolute angle |
| 5. Move perch pose | `python3 manually_calibrate_robot.py servo WRIST 95` | `servo` `WRIST` | Servo reaches safe pose |
| 5. Move perch pose | `python3 manually_calibrate_robot.py servo ELBOW 125` | `servo` `ELBOW` | Servo reaches safe pose |
| 5. Move perch pose | `python3 manually_calibrate_robot.py servo TWIST 90` | `servo` `TWIST` | Servo reaches safe pose |
| 5. Save perch pose | `python3 manually_calibrate_robot.py save-perch elbow 125` | `calibrate` `perch_elbow_angle` | Reply includes `PERCH_ELBOW_ANGLE` |
| 5. Save perch pose | `python3 manually_calibrate_robot.py save-perch wrist 95` | `calibrate` `perch_wrist_angle` | Reply includes `PERCH_WRIST_ANGLE` |
| 5. Save perch pose | `python3 manually_calibrate_robot.py save-perch twist 90` | `calibrate` `perch_twist_angle` | Reply includes `PERCH_TWIST_ANGLE` |
| 5. Optional perch distances | `python3 manually_calibrate_robot.py save-perch min 0` | `calibrate` `perch_min` | Distance landmark saved |
| 5. Optional perch distances | `python3 manually_calibrate_robot.py save-perch mid 60` | `calibrate` `perch_mid` | Distance landmark saved |
| 5. Optional perch distances | `python3 manually_calibrate_robot.py save-perch max 120` | `calibrate` `perch_max` | Distance landmark saved |
| 5. Test perch | `python3 manually_calibrate_robot.py perch` | `perch` | Moves to saved/default perch |
| 6. Hover min pose | `python3 manually_calibrate_robot.py servo ELBOW 126` | `servo` `ELBOW` | Gripper hovers near distance `0` |
| 6. Hover min pose | `python3 manually_calibrate_robot.py servo WRIST 0` | `servo` `WRIST` | Gripper hovers near distance `0` |
| 6. Save hover min | `python3 manually_calibrate_robot.py save-hover min 0` | `calibrate` `hover_over_min` | `hover_over_min` saved |
| 6. Hover mid pose | `python3 manually_calibrate_robot.py servo ELBOW 132` | `servo` `ELBOW` | Gripper hovers near distance `60` |
| 6. Hover mid pose | `python3 manually_calibrate_robot.py servo WRIST 46` | `servo` `WRIST` | Gripper hovers near distance `60` |
| 6. Save hover mid | `python3 manually_calibrate_robot.py save-hover mid 60` | `calibrate` `hover_over_mid` | `hover_over_mid` saved |
| 6. Hover max pose | `python3 manually_calibrate_robot.py servo ELBOW 165` | `servo` `ELBOW` | Gripper hovers near distance `120` |
| 6. Hover max pose | `python3 manually_calibrate_robot.py servo WRIST 160` | `servo` `WRIST` | Gripper hovers near distance `120` |
| 6. Save hover max | `python3 manually_calibrate_robot.py save-hover max 120` | `calibrate` `hover_over_max` | `hover_over_max` saved |
| 7. Test IK min | `python3 manually_calibrate_robot.py ik 0 --z 0` | `controlik` | Moves near min hover point |
| 7. Test IK mid | `python3 manually_calibrate_robot.py ik 60 --z 0` | `controlik` | Moves near mid hover point |
| 7. Test IK max | `python3 manually_calibrate_robot.py ik 120 --z 0` | `controlik` | Moves near max hover point |
| 8. Optional z min | `python3 manually_calibrate_robot.py save-z min 30` | `calibrate` `hover_min_120` | First reachable z=50 point saved |
| 8. Optional z mid | `python3 manually_calibrate_robot.py save-z mid 75` | `calibrate` `hover_mid_120` | Middle reachable z=50 point saved |
| 8. Optional z max | `python3 manually_calibrate_robot.py save-z max 120` | `calibrate` `hover_max_120` | Upper z-plane max saved |
| 8. Test z midpoint min | `python3 manually_calibrate_robot.py ik 15 --z 25` | `controlik` | Moves on the trapezoid slanted boundary |
| 8. Test z max | `python3 manually_calibrate_robot.py ik 75 --z 50` | `controlik` | Moves to reachable upper z plane |
| 9. Start stencil | `python3 manually_calibrate_robot.py stencil-start` | `stencilCalibrate` `START` | Message names first stencil point |
| 9. Run point | `python3 manually_calibrate_robot.py stencil-run` | `stencilCalibrate` `RUN_POINT` | Peg grab advances point |
| 9. Adjust miss | `python3 manually_calibrate_robot.py stencil-adjust --rotation 2.0 --distance -3.0` | `stencilCalibrate` `ADJUST` | Current point correction saved |
| 9. Check stencil | `python3 manually_calibrate_robot.py stencil-status` | `stencilCalibrate` `STATUS` | Current phase/point visible |
| 10. Final check | `python3 manually_calibrate_robot.py check` | `calibrationvalues` | Final checklist passes |

## Raw MQTT Examples

Use these as direct-publish examples when not using the helper.

Base profile:

```json
{"sender":"manual_calibrator","action_id":"base_profile_001","action":"baseRotate","controlType":"CALIBRATE_PROFILE"}
```

Base profile with neutral override:

```json
{"sender":"manual_calibrator","action_id":"base_profile_090","action":"baseRotate","controlType":"CALIBRATE_PROFILE","neutralServoAngle":90}
```

Servo move:

```json
{"sender":"manual_calibrator","action_id":"servo_wrist_perch_001","action":"servo","servoName":"WRIST","position":95}
```

Table-level IK hover save:

```json
{"sender":"manual_calibrator","action_id":"save_hover_mid_001","action":"calibrate","calibration_type":"hover_over_mid","distance":60}
```

Legacy-named z=50 top-edge save:

```json
{"sender":"manual_calibrator","action_id":"save_hover_z50_mid_001","action":"calibrate","calibration_type":"hover_mid_120","distance":75}
```

IK move:

```json
{"sender":"manual_calibrator","action_id":"ik_test_015_z25","action":"controlik","distance":15,"z_height":25}
```

Stencil command:

```json
{"sender":"manual_calibrator","action_id":"stencil_adjust_001","action":"stencilCalibrate","command":"ADJUST","rotationNudgeDegrees":2.0,"distanceNudgeMm":-3.0}
```

## Step Notes

### Base Rotation

`CALIBRATE_PROFILE` finds true north, measures left/right counts per revolution, balances neutral, and marks the base position trusted. It may rotate the base multiple full turns. If it fails, do not continue to stencil calibration.

Pass fields to look for in `base_rotation`:

- `calibrated:true`
- `profileCalibrated:true`
- `positionTrusted:true`
- `calibrationBalanced:true`
- `leftCountsPerRev` greater than `0`
- `rightCountsPerRev` greater than `0`
- `calibrationPhase:"complete"`

Use `python3 manually_calibrate_robot.py base-profile --neutral 88` or nearby values if the continuous servo does not stop around `90`.

### Perch Pose

Perch is the safe resting pose. Firmware defaults are usable, but saving a real safe pose makes later calibration more predictable.

If normal elbow/wrist moves are blocked by the table guard during manual setup, use live mode carefully:

```bash
python3 manually_calibrate_robot.py servo ELBOW 125 --live
```

Live mode uses `action_id:"live"` and bypasses normal safety behavior, so use it only for careful manual recovery.

### IK Hover Points

Hover points are the main table-level reach calibration. The stencil workflow uses distances `0`, `60`, and `120` mm, so those are the recommended landmarks.

For each point:

1. Move `ELBOW` and `WRIST` until the gripper hovers safely over the target reach.
2. Save the matching `hover_over_min`, `hover_over_mid`, or `hover_over_max`.
3. Keep saved distances monotonic: min < mid < max.

Example angles are only starting points. Use whatever angles fit your robot without hitting the table. After hover points are saved, the table guard becomes active for normal elbow/wrist moves.

### Optional Z-Height

Skip this for a basic table-level grab setup unless you need `z_height > 0`.

Current z model:

| Runtime z height | Meaning |
| --- | --- |
| `0` | Default/min table plane |
| `25` | Midpoint through the trapezoid workspace |
| `50` | Reachable upper edge; min distance is the first high reachable point |

The MQTT calibration names `hover_min_120`, `hover_mid_120`, and `hover_max_120` are preserved for compatibility. They now represent the reachable upper edge at `50` mm, not a 120 mm runtime plane or a full-width rectangle.

### Stencil Calibration

Stencil calibration combines base angle and IK reach. `START` moves the arm to perch, homes the base to true north by rotating `RIGHT` at `veryslow`, then verifies base absolute angle readiness.

The current session has 15 checks:

| Group | Angle | z height | Distances |
| --- | --- | --- | --- |
| z=0 offset points | `-30`, `0`, `30` degrees | `0` mm | `0`, `60`, `120` mm at each angle |
| z=50 validation | `0` degrees | `50` mm | `30`, `75`, `120` mm |
| z=25 validation | `0` degrees | `25` mm | `15`, `60`, `120` mm |

`RUN_POINT` opens the gripper, moves the arm to perch before required lane rotations, moves the base only when the stencil lane angle changes, moves IK with offsets disabled, and tries to grab. Repeated same-lane points skip base rotation unless `rotationNudgeDegrees` changes the target. If it misses, use `ADJUST`, then run the same point again. If it grabs but the point was visibly off, use `ADJUST_PREVIOUS` to apply the nudge to the just-completed point and retry it immediately.

Adjustment meaning:

| Field | Positive value | Negative value |
| --- | --- | --- |
| `rotationNudgeDegrees` | Target farther RIGHT | Target farther LEFT |
| `distanceNudgeMm` | Reach farther/deeper | Reach shallower/closer |

Final stencil completion saves:

- `rot_off_deg`
- `ik_off_mm`
- `st_map` with all 15 point diagnostics
- Average runtime offsets from the 9 z=0 offset points only; z=25/z=50 checks are validation diagnostics

## Final Checklist

After `python3 manually_calibrate_robot.py check`, confirm:

- `base_rotation_ready:true`
- `base_rotation_calibrated:true`
- `base_rotation_profileCalibrated:true`
- `base_rotation_lastValid:true`
- `ik_hover_calibrated:true`
- `stencil_calibrated:true`
- `motion_calibration_ready:true`
- `initial_calibration_ready:true`
- `rot_off_deg` is a number
- `ik_off_mm` is a number
- `perch_effective` looks safe for your robot

Optional z-height fields:

- `ik_z50_calibrated:true`
- `ik_z120_calibrated:true` may also appear because old MQTT field names are preserved

Quick re-test:

```bash
python3 manually_calibrate_robot.py base-angle 0
python3 manually_calibrate_robot.py ik 60 --z 0
python3 manually_calibrate_robot.py ik 60 --z 25
python3 manually_calibrate_robot.py ik 60 --z 50
python3 manually_calibrate_robot.py open-gripper
```

## Troubleshooting

| Problem | Check / action |
| --- | --- |
| No MQTT reply | Confirm ESP32 `ready`, topic `{mqtt_user}/test`, valid JSON, non-firmware sender, and matching `action_id` |
| Base profile fails | Check active-low true-north switch on GPIO14, AS5600 on GPIO1, free rotation, power, and neutral override near `90` |
| Base angle fails | Re-run `base-profile`; stencil requires trusted absolute position |
| IK/servo blocked | Check hover point order, perch safety side, and whether a collision pose was accidentally saved |
| Stencil `START` fails | Confirm `base_rotation_ready`, `base_rotation_calibrated`, `base_rotation_profileCalibrated`, and `base_rotation_lastValid` |
| Gripper never succeeds | Check active-low stop pins GPIO45/GPIO46 and peg contact with a stop switch |
| Noisy messages | Ignore `count`, `heartbeat`, `debug`, and messages with a different `action_id` |
