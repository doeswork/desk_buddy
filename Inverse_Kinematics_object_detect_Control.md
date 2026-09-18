# Inverse kinematics from object detection

Desk Buddy has one mono camera, so a flat paper target does not look like a
straight rectangular image grid. The nine calibration dots describe how the
robot's real workspace appears in that camera.

## The nine-point calibration

Place nine dots in three physical lanes:

| Lane | Robot base angle | Dots measured from the base |
| --- | ---: | ---: |
| left | `-30°` | `0`, `60`, `120 mm` |
| center | `0°` | `0`, `60`, `120 mm` |
| right | `+30°` | `0`, `60`, `120 mm` |

The camera detects the nine dark circles and labels each one by lane and
distance. The labels are image coordinates, not a claim that the dots form a
straight image grid. Because of perspective and the camera's position, the
three lanes can bow into arches and the whole usable area can look like a cone.

The historical implementation is in the `bigger-rewrite-vision` branch:

```text
studio/services/vision/ik_control/calibration_photo.py
studio/services/vision/ik_control/calibration.py
```

## Building the camera-space grid

The nine anchors are interpolated in two directions:

1. Along each lane, interpolate image position over `0/60/120 mm`.
2. Between the three lanes, interpolate over `-30/0/+30°`.

Quadratic interpolation is used in both directions so the mapped lines can
curve with the mono-camera projection. The service then samples the mapping at
`5°` and `5 mm` intervals. That creates 288 quadrilateral cells covering
`-30..+30°` and `0..120 mm`.

Each cell remembers its image polygon and its representative robot command:

```text
image polygon → base rotation angle + IK distance
```

This is why the system can use a detected object's image location without
pretending that pixels have a universal millimeter scale.

## From a zero-shot box to x/y

The zero-shot detector returns one or more bounding boxes. The highest-confidence
box is selected for the first deterministic path, while every box remains in
the result for the UI.

The lookup point is:

```text
x = (box_left + box_right) / 2
y = box_bottom - 10% × box_height
```

The x coordinate is the object's horizontal center. The y coordinate is close
to the bottom of the object because that is a better approximation of where it
touches the table than the visual center. The grid cell containing that point
provides:

```text
x → base rotation angle
y → IK distance/depth
z → 0 mm for this first milestone
```

If the point falls outside the calibrated polygon, the target is rejected. It
must not be clamped to a nearby cell for physical motion.

## MQTT and motion sequence

The current repository uses the robot's MQTT topics:

```text
Studio              → {robot}/commands   calibrate_depth / detect_object
ESP32               → {robot}/photos     correlated JPEG frame
Vision worker       → {robot}/vision     calibration or detection result
ESP32               → {robot}/events     lifecycle and command acknowledgements
```

Calibration is a photo operation. `calibrate_depth` is the existing firmware
photo action name; it does not mean that a depth model runs during this step.
The Vision worker detects the dots, builds the grid, stores the profile by
robot, and returns the points/grid/profile ID.

For a movement request, the controller must first verify firmware's
`motion_calibration_ready` state and validate the target. It then sends one
command at a time and waits for the matching terminal event:

1. `baseRotate` to the calibrated angle (`DEGREES`, left/right direction);
2. `controlik` with `distance` equal to the grid's calibrated y value and
   `z_height: 0`;
3. `gripper: GRAB` only when explicitly requested.

Every child command gets a new action ID. A failed publish, mismatched reply,
timeout, or lost MQTT connection aborts the sequence; uncertain physical
commands are never retried. A normal detection preview does not move the robot.

## Adding vertical depth later

The upcoming depth model should run on the same captured image and selected
object. It will provide evidence for `z`, while the nine-point grid continues
to provide calibrated base angle and table distance. A relative depth map is
not automatically millimeters, so a separate robot/depth scale calibration and
validity checks are required before sending a nonzero `z_height` to firmware.
