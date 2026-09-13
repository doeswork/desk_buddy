"""A side-on drawing of Desk Buddy in the shape one hover point asks for.

The IK calibration is a shape-matching task: the user bends the arm until the
gripper hovers over a mark on the table, then records the angles that produced
it. What makes that hard is that "hover_mid_120" says nothing about what the
arm should *look* like, so this draws it — the target pose ghosted behind the
arm's live one, both from the same geometry.

Deliberately a drawing rather than a photograph. A photo would need an asset
pipeline this app does not have, would not track the theme, and — worse — could
only ever show one arm at one angle, where the whole point here is to show two
poses at once and let the live one move.

The proportions come from the real arm: a short base, a long upper segment,
a shorter forearm, and a stubby gripper. Exactness does not matter and would
be false precision anyway — every robot's servo horns are mounted slightly
differently, which is the entire reason this calibration exists. What has to
be right is the *shape*: folded in and low for min, reaching out and flat for
max, and visibly higher off the table for the z=50 points.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


@dataclass(frozen=True)
class Pose:
    """One hover point: the shape to make, and what to say about it."""

    elbow: float
    wrist: float
    twist: float
    distance: float
    height: float
    summary: str
    hint: str


# The shape each point asks for, solved from the real segment lengths below
# so the drawn gripper lands exactly on the mark named in the text. These are
# a shape to aim at, never a value to save: the walkthrough is explicit that
# the angles which actually work differ per robot — servo horns are mounted
# slightly differently on every build — and the number recorded is whatever
# the arm was really at when captured.
#
# `elbow` here is the drawing's screen angle, measured from straight up and
# swinging forward over the table, not a servo command.
#
# Every `distance` is measured out from the *edge* of the turntable, not its
# centre — see BASE_RADIUS. That is what makes zero reach a real pose: the
# gripper parks just off the base's right side, where the arm actually rests
# when it is folded all the way in, rather than impaled through the middle
# of its own base.
POSES = {
    "hover_over_min": Pose(
        elbow=58.9, wrist=42.1, twist=90, distance=0, height=0,
        summary="Folded right in — gripper down beside the base, on the table.",
        hint="Zero reach: the gripper is as close to the base as it goes, "
             "just off its right edge and resting at table level rather "
             "than out in front.",
    ),
    "hover_over_mid": Pose(
        elbow=57.8, wrist=72.0, twist=90, distance=60, height=0,
        summary="Reaching out, gripper down at the 60 mm mark.",
        hint="Forearm angled down so the gripper is at table level, just "
             "touching the 60 mm mark rather than hovering above it.",
    ),
    "hover_over_max": Pose(
        elbow=76.3, wrist=118.5, twist=90, distance=120, height=0,
        summary="Fully extended, gripper down at the 120 mm mark.",
        hint="The furthest the gripper reaches while still touching the "
             "table. Stop before the arm strains or the base lifts.",
    ),
    "hover_min_120": Pose(
        elbow=12.1, wrist=40.6, twist=90, distance=30, height=50,
        summary="Close in at the 30 mm mark, lifted 50 mm off the table.",
        hint="The first point the gripper can reach at the upper edge — "
             "close in, and noticeably higher than the z=0 poses.",
    ),
    "hover_mid_120": Pose(
        elbow=31.7, wrist=70.4, twist=90, distance=75, height=50,
        summary="Reaching to the 75 mm mark, held 50 mm up.",
        hint="Same reach as mid, raised. The forearm points out and level "
             "rather than down.",
    ),
    "hover_max_120": Pose(
        elbow=52.8, wrist=106.7, twist=90, distance=120, height=50,
        summary="Fully extended to the 120 mm mark, held 50 mm up.",
        hint="The far upper corner of the workspace. Furthest reach that "
             "still holds the gripper off the table.",
    ),
}

# Segment lengths, in the drawing's own units — measured off the real robot
# at 20 units to the inch, so the proportions on screen are the machine's own
# rather than a plausible-looking guess.
#
#   circular base        1.5 in   the turntable the whole arm stands on
#   base top -> shoulder 2.5 in   the platform that carries the elbow servo
#   shoulder -> wrist    3.5 in   the upper arm
#   wrist -> fingertips  4.5 in   forearm, gripper body and jaws together
#
# The forearm assembly being *longer* than the upper arm is the shape that
# was wrong before: the drawing had it shorter, which is what made the arm
# read as a stick figure of some other robot. It is also why the arm can
# fold right back over its own base to reach in to zero — a short forearm
# cannot do that, and the old drawing had to pretend otherwise.
UNITS_PER_INCH = 20.0

BASE_HEIGHT = 1.5 * UNITS_PER_INCH      # the turntable itself
SHOULDER_HEIGHT = 2.5 * UNITS_PER_INCH  # table to the elbow pivot
UPPER_ARM = 3.5 * UNITS_PER_INCH        # elbow pivot to wrist pivot
HAND = 4.5 * UNITS_PER_INCH             # wrist pivot to fingertips

# The hand is drawn as a forearm and a gripper, splitting the one measured
# 4.5 in between them. Where the split falls is cosmetic — the fingertip
# lands in the same place either way — so it is chosen to look like the
# photo: a long dark forearm, then a stubby two-jaw gripper.
FOREARM = HAND * 0.68
GRIPPER = HAND - FOREARM

# The turntable's radius — 3.4 in across on the real machine. Load-bearing
# rather than decoration: every calibration distance is measured out from
# this edge, so the 0 mm mark sits here and not at the base's centre. Zero
# reach means the gripper parked beside the base, which is where the folded
# arm actually puts it.
BASE_RADIUS = 1.7 * UNITS_PER_INCH


class ArmPoseView(QWidget):
    """The target shape, with the arm's live pose drawn over it.

    Both are the same drawing routine at different angles — the user is
    matching one to the other, so anything that made them look different in
    kind would work against the only job this widget has.
    """

    def __init__(self, pose: Pose, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.pose = pose
        self._live: tuple[float, float, float] | None = None
        self.setMinimumSize(210, 150)

    def set_live(self, elbow: float, wrist: float, twist: float) -> None:
        """Show where the arm actually is, in *screen* degrees.

        Screen degrees, not servo degrees. The caller converts, because only
        it knows this robot's mapping — see `servo_frame`. Passing a raw
        heartbeat angle here is what drew the live arm through the table.
        """
        live = (elbow, wrist, twist)
        if live != self._live:
            self._live = live
            self.update()

    def clear_live(self) -> None:
        """Stop drawing the live arm — used while the mapping is unknown."""
        if self._live is not None:
            self._live = None
            self.update()

    # ---- drawing ---------------------------------------------------------
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Colours come from the widget's own palette rather than a theme
        # import, so the drawing follows a theme change for free.
        colors = self.palette()
        ink = colors.windowText().color()
        muted = QColor(ink)
        muted.setAlphaF(0.30)
        accent = colors.highlight().color()

        # The legend takes a strip off the top, so the arms never overlap it.
        area = QRectF(self.rect()).adjusted(10, 30, -10, -18)
        # The origin sits far enough in for the turntable's left half and
        # for the arm rocking back behind it, but no further: every pixel
        # left of the base is width the reach marks do not get.
        behind = max(BASE_RADIUS, self._behind())
        origin = QPointF(area.left(), area.bottom() - 14)
        # Sized to what this pose actually occupies, not to the arm held
        # straight up. No calibration pose stands anywhere near that tall,
        # so dividing by the summed segments left the drawing marooned in a
        # third of its own widget with the machine shrunk at the bottom.
        scale = min(
            (area.width() * 0.94) / (behind + self._span()),
            (area.height() * 0.90) / self._rise(),
        )
        origin.setX(area.left() + behind * scale)

        self._draw_table(painter, area, origin, scale, ink, muted)
        self._draw_body(painter, origin, scale, ink)
        # Target first, so the live arm reads as sitting on top of it.
        self._draw_arm(
            painter, origin, scale,
            self.pose.elbow, self.pose.wrist,
            QPen(muted, 5.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin),
        )
        if self._live is not None:
            elbow, wrist, _twist = self._live
            self._draw_arm(
                painter, origin, scale, elbow, wrist,
                QPen(accent, 2.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin),
            )
        self._draw_legend(painter, QRectF(self.rect()), ink, muted, accent)
        painter.end()

    def _extent(self) -> tuple[float, float]:
        """How far out and how far up this pose reaches, in drawing units.

        Both arms are measured, not just the target: the live arm can be
        anywhere while the user is posing it, and a frame fitted to the
        ghost alone would let the real one wander off the edge.
        """
        out = up = 0.0
        poses = [(self.pose.elbow, self.pose.wrist)]
        if self._live is not None:
            poses.append((self._live[0], self._live[1]))
        for elbow, wrist in poses:
            upper = math.radians(elbow)
            x = math.sin(upper) * UPPER_ARM
            z = SHOULDER_HEIGHT + math.cos(upper) * UPPER_ARM
            out = max(out, abs(x)); up = max(up, z)
            fore = upper + math.radians(180 - wrist)
            x += math.sin(fore) * HAND
            z += math.cos(fore) * HAND
            out = max(out, abs(x)); up = max(up, z)
        return out, up

    def _behind(self) -> float:
        """How far the arm swings back behind the base, in drawing units."""
        back = 0.0
        poses = [(self.pose.elbow, self.pose.wrist)]
        if self._live is not None:
            poses.append((self._live[0], self._live[1]))
        for elbow, wrist in poses:
            upper = math.radians(elbow)
            x = math.sin(upper) * UPPER_ARM
            back = min(back, x)
            fore = upper + math.radians(180 - wrist)
            back = min(back, x + math.sin(fore) * HAND)
        return -back

    def _mark(self, millimetres: float) -> float:
        """Where a calibration distance sits, in units out from the centre.

        Distances are quoted from the turntable's edge, so every mark is the
        base's radius further out than its number suggests. One method does
        the conversion for the reach marks, the height plane and the frame
        alike — three copies of `+ BASE_RADIUS` is how a drawing ends up
        with its ruler and its arm disagreeing.
        """
        return BASE_RADIUS + millimetres * self._mm_to_units()

    def _span(self) -> float:
        """Horizontal room to reserve: the pose, or the 120 mm mark."""
        out, _up = self._extent()
        return max(out, self._mark(120.0))

    def _rise(self) -> float:
        """Vertical room to reserve, never less than the machine itself.

        The whole arm is measured, so a pose that throws the elbow high —
        the close-in raised point rocks right back — is framed rather than
        cropped at the top.
        """
        _out, up = self._extent()
        return max(up, SHOULDER_HEIGHT + UPPER_ARM * 0.5)

    def _draw_legend(self, painter, rect, ink, muted, accent) -> None:
        """Name the two arms, in the colours they are actually drawn in.

        Without this the drawing is a puzzle: two overlapping arms with no
        way to tell which one the user is supposed to be moving. A key in
        the body text would work too, but only this puts the words next to
        the thing they name.
        """
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 2))
        painter.setFont(font)
        metrics = painter.fontMetrics()

        entries = [("Target shape", muted, 5.0)]
        if self._live is not None:
            entries.append(("Arm now", accent, 2.6))

        x = rect.left() + 10
        y = rect.top() + 12
        swatch = 16
        for label, color, width in entries:
            painter.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(QPointF(x, y), QPointF(x + swatch, y))
            x += swatch + 5
            painter.setPen(QPen(ink, 1.0))
            painter.drawText(
                QRectF(x, y - 8, metrics.horizontalAdvance(label) + 4, 16),
                Qt.AlignLeft | Qt.AlignVCenter,
                label,
            )
            x += metrics.horizontalAdvance(label) + 14

    def _draw_body(self, painter, origin, scale, ink) -> None:
        """The turntable and the post the arm is bolted to.

        Neither moves with the pose, so both are drawn once, under both
        arms, in a solid quiet fill — the photo's black plastic. Without
        them the arm floated on a hairline and read as a stick figure; the
        wide circular base is the most recognisable thing about the real
        machine from the side.
        """
        # Quiet enough to stay scenery. The arm is the subject and the
        # base is what it stands on; drawn at the ghost arm's own weight the
        # turntable read as the loudest thing in the picture purely because
        # it is the widest.
        body = QColor(ink)
        body.setAlphaF(0.10)
        painter.setPen(Qt.NoPen)
        painter.setBrush(body)

        # The turntable: as wide as the real one, seen edge-on.
        painter.drawRoundedRect(
            QRectF(
                origin.x() - BASE_RADIUS * scale,
                origin.y() - BASE_HEIGHT * scale,
                BASE_RADIUS * 2 * scale,
                BASE_HEIGHT * scale,
            ),
            2.0, 2.0,
        )
        # The post carrying the elbow servo, up to the shoulder pivot.
        post = UNITS_PER_INCH * 0.5 * scale
        painter.drawRoundedRect(
            QRectF(
                origin.x() - post / 2,
                origin.y() - SHOULDER_HEIGHT * scale,
                post,
                (SHOULDER_HEIGHT - BASE_HEIGHT) * scale + 1,
            ),
            1.5, 1.5,
        )
        painter.setBrush(Qt.NoBrush)

    def _draw_table(self, painter, area, origin, scale, ink, muted) -> None:
        """The table line, and the reach mark this pose aims for."""
        pen = QPen(muted, 1.2)
        painter.setPen(pen)
        painter.drawLine(
            QPointF(area.left(), origin.y()),
            QPointF(area.right(), origin.y()),
        )

        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 2))
        painter.setFont(font)

        # Reach marks at the three landmark distances the stencil workflow
        # uses, with this pose's own target picked out.
        for millimetres in (0, 60, 120):
            x = origin.x() + self._mark(millimetres) * scale
            if x > area.right():
                continue
            target = millimetres == self.pose.distance
            painter.setPen(QPen(ink if target else muted, 1.6 if target else 1.0))
            painter.drawLine(QPointF(x, origin.y() - 4), QPointF(x, origin.y() + 4))
            painter.drawText(
                QRectF(x - 22, origin.y() + 5, 44, 14),
                Qt.AlignHCenter | Qt.AlignTop,
                f"{millimetres}",
            )

        # The z=50 plane, drawn only for the poses that live on it — an
        # always-present line would imply every point is a height target.
        if self.pose.height:
            y = origin.y() - self.pose.height * self._mm_to_units() * scale
            painter.setPen(QPen(muted, 1.0, Qt.DashLine))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))

    @staticmethod
    def _mm_to_units() -> float:
        """Drawing units per millimetre — the real conversion, not a fudge.

        The segment lengths above are the robot's own, in units of 1/20th of
        an inch, so a millimetre is a millimetre and the reach marks land
        where the real gripper would land. The old drawing needed a fitted
        constant here because its proportions were invented: the arm could
        only touch 120 mm by being stretched dead straight, so the scale had
        to be bent until that looked right. This arm reaches roughly 190 mm,
        which puts the 120 mm mark comfortably inside its working range —
        where it actually is on the desk.
        """
        return UNITS_PER_INCH / 25.4

    def _draw_arm(self, painter, origin, scale, elbow, wrist, pen) -> None:
        """One arm, from the two screen angles that decide its shape.

        Both angles are already in the drawing's own space — elbow measured
        from straight up, wrist relative to the upper arm. This routine does
        no servo conversion and must not start doing one: the target pose and
        the live arm both arrive here in screen degrees precisely so they can
        be compared, which is the only thing this widget is for.
        """
        painter.setPen(pen)

        # The arm starts at the shoulder. The base below it is the same for
        # both poses, so it is drawn once with the table rather than twice
        # here — two stacked copies of an unmoving part only muddied the one
        # thing this widget is for, which is the difference between the two.
        base_top = QPointF(origin.x(), origin.y() - SHOULDER_HEIGHT * scale)

        # Screen angles are measured from straight up, swinging forward over
        # the table: 0 stands the upper arm up, 180 lays it flat forward.
        upper = math.radians(elbow)
        joint = QPointF(
            base_top.x() + math.sin(upper) * UPPER_ARM * scale,
            base_top.y() - math.cos(upper) * UPPER_ARM * scale,
        )
        painter.drawLine(base_top, joint)

        # The wrist bends relative to the upper arm: 180 continues straight
        # on, and anything less folds the forearm down toward the table.
        fore = upper + math.radians(180 - wrist)
        hand = QPointF(
            joint.x() + math.sin(fore) * FOREARM * scale,
            joint.y() - math.cos(fore) * FOREARM * scale,
        )
        painter.drawLine(joint, hand)

        # The gripper: a short stem carrying on in the forearm's direction,
        # then two jaws splayed off its end.
        #
        # The stem matters. Drawing the jaws straight off the wrist — which
        # is the obvious way — leaves the visible metal stopping short of
        # where the gripper actually is, because a splayed line covers less
        # ground than a straight one. Every target pose then read as falling
        # short of its own reach mark while being, in fact, exactly on it.
        stem = GRIPPER * 0.55
        wrist_end = QPointF(
            hand.x() + math.sin(fore) * stem * scale,
            hand.y() - math.cos(fore) * stem * scale,
        )
        painter.drawLine(hand, wrist_end)

        jaw_length = GRIPPER - stem
        spread = math.radians(22)
        for offset in (spread, -spread):
            jaw = fore + offset
            painter.drawLine(
                wrist_end,
                QPointF(
                    wrist_end.x() + math.sin(jaw) * jaw_length * scale,
                    wrist_end.y() - math.cos(jaw) * jaw_length * scale,
                ),
            )

        # A dot at the base so the arm is visibly anchored rather than
        # floating at whatever angle it happens to be.
        path = QPainterPath()
        path.addEllipse(origin, 3.0, 3.0)
        painter.fillPath(path, pen.color())

