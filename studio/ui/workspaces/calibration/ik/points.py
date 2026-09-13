"""What the IK step measures: the six hover points, and how they are stored.

Constants only. They are shared by the page, the hover-point card, and the
tests, and a value that three modules agree on has to live somewhere none of
them owns — otherwise the card imports the page for a tuple and the page
imports the card for a widget.
"""

from __future__ import annotations

# Where the per-hover-point captures live inside the IK step's values. A
# nested key rather than six top-level ones: `results()` flattens whatever
# the firmware replies into the same dict, and a name it will never produce
# is what keeps the two from colliding.
CAPTURED_POINTS = "capturedPoints"

GROUPS = (
    ("Table level (z = 0 mm)", (
        ("hover_over_min", "Min"),
        ("hover_over_mid", "Mid"),
        ("hover_over_max", "Max"),
    )),
    ("Raised (z = 50 mm) — optional", (
        ("hover_min_120", "Min"),
        ("hover_mid_120", "Mid"),
        ("hover_max_120", "Max"),
    )),
)

# The joints this page lets you drive. Twist is not one of them: it rotates
# the gripper about its own axis, which changes how the hand is oriented but
# not how far out or how high up it is — and reach and height are the whole
# of what these six points measure. A slider that cannot move the thing
# being measured is a slider that can only be set wrong, so the arm is posed
# on two axes here and twist is calibrated once, in the Base + Perch step.
JOINTS = (("elbow", "ELBOW"), ("wrist", "WRIST"))

# What every hover snapshot reports for TWIST. Fixed rather than read from
# the arm so the six points cannot disagree about it: a snapshot taken with
# the gripper accidentally rotated would otherwise bake that rotation into
# the calibration. 90° is the neutral, square-on wrist the poses are drawn
# in and the value the walkthrough's worked example uses.
TWIST_ANGLE = 90
