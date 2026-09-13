"""Turning servo degrees into the drawing's screen degrees.

`arm_pose` draws in screen angles: measured from straight up, swinging forward
over the table. A heartbeat reports servo angles, which are a different thing —
0-180 on a horn mounted however this particular robot's horn was mounted. The
two were fed through one drawing routine as if they were interchangeable, which
put the live arm 84 mm underneath the table while the real machine sat in the
shape the target asked for.

There is no constant to correct that with. How a horn is mounted is exactly
what the IK step measures, so Studio cannot know it before the step has run.
What it *can* do is read it back out of the captures: a capture pairs a servo
reading with the target shape the user had just matched, so two captures on a
joint give the line through them.

    screen = offset + scale * servo

Two points, one line, per joint. Not because the servo linkage is perfectly
linear, but because two captures cannot support anything richer, and a fitted
line through the user's own poses is the honest reading of what they measured.
Below two captures there is no mapping, and `Frame.known` is False — callers
are expected to draw nothing rather than guess.
"""

from __future__ import annotations

from dataclasses import dataclass

from .arm_pose import POSES

# Below this the two captures are effectively the same pose, and the line
# through them is a division by almost zero — a scale of several thousand
# that throws the live arm off the widget. The user needs two *different*
# shapes for a mapping; degrees apart is how that is checked.
MIN_SERVO_SPREAD = 5.0

# A fitted scale outside this is not a calibration, it is a bad fit: the two
# captures disagree with their own target shapes. Real horn mountings invert
# (-1) or run direct (+1) with some gearing either side of that.
MAX_ABS_SCALE = 4.0


@dataclass(frozen=True)
class Joint:
    """The line from one joint's servo degrees to the drawing's."""

    offset: float
    scale: float

    def to_screen(self, servo: float) -> float:
        return self.offset + self.scale * servo


@dataclass(frozen=True)
class Frame:
    """How to draw this robot's live arm, or that it cannot be drawn yet."""

    elbow: Joint | None = None
    wrist: Joint | None = None

    @property
    def known(self) -> bool:
        """Whether a live pose can be drawn in the target's own space."""
        return self.elbow is not None and self.wrist is not None

    def to_screen(self, elbow: float, wrist: float) -> tuple[float, float] | None:
        """The live pose in screen degrees, or None when unmapped."""
        if self.elbow is None or self.wrist is None:
            return None
        return self.elbow.to_screen(elbow), self.wrist.to_screen(wrist)


def _fit(pairs: list[tuple[float, float]]) -> Joint | None:
    """The line through captured (servo, screen) points, or None.

    Two captures give a line exactly. More than two are least-squares fitted,
    so a third capture refines the mapping rather than replacing it — the
    user recapturing one pose should not throw away the other two.
    """
    if len(pairs) < 2:
        return None

    servos = [servo for servo, _screen in pairs]
    if max(servos) - min(servos) < MIN_SERVO_SPREAD:
        return None

    count = len(pairs)
    mean_servo = sum(servos) / count
    mean_screen = sum(screen for _servo, screen in pairs) / count
    spread = sum((servo - mean_servo) ** 2 for servo in servos)
    if spread <= 0:
        return None

    scale = sum(
        (servo - mean_servo) * (screen - mean_screen) for servo, screen in pairs
    ) / spread
    if not 0 < abs(scale) <= MAX_ABS_SCALE:
        return None

    return Joint(offset=mean_screen - scale * mean_servo, scale=scale)


def frame_from_captures(captures: dict) -> Frame:
    """Build the mapping from this robot's captured hover points.

    `captures` is `{calibration_type: {"elbow": servo, "wrist": servo}}` —
    what the arm actually read when the user said the shape matched. The
    screen angle each one corresponds to is the target pose they were
    matching, which is already in `POSES`.
    """
    elbow_pairs: list[tuple[float, float]] = []
    wrist_pairs: list[tuple[float, float]] = []

    for calibration_type, angles in sorted(captures.items()):
        pose = POSES.get(calibration_type)
        if pose is None or not isinstance(angles, dict):
            continue
        elbow, wrist = angles.get("elbow"), angles.get("wrist")
        if isinstance(elbow, (int, float)) and not isinstance(elbow, bool):
            elbow_pairs.append((float(elbow), pose.elbow))
        if isinstance(wrist, (int, float)) and not isinstance(wrist, bool):
            wrist_pairs.append((float(wrist), pose.wrist))

    return Frame(elbow=_fit(elbow_pairs), wrist=_fit(wrist_pairs))
