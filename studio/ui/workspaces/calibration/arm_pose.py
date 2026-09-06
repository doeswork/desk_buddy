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


# Starting angles from CALIBRATE_ROBOT_WALKTHROUGH.md's worked example, which
# is where the "hover max" pair (ELBOW 165 / WRIST 160) comes from. They are
# a shape to aim at, never a value to save: the walkthrough is explicit that
# the angles that actually work differ per robot, and the saved number is
# whatever the arm was really at.
POSES = {
    "hover_over_min": Pose(
        elbow=10, wrist=20, twist=90, distance=0, height=0,
        summary="Folded in, gripper just above the base.",
        hint="The closest the gripper can hover without touching the base. "
             "Elbow well back, forearm angled down.",
    ),
    "hover_over_mid": Pose(
        elbow=17, wrist=62, twist=90, distance=60, height=0,
        summary="Half extended, gripper over the 60 mm mark.",
        hint="Elbow forward of vertical, forearm reaching out and slightly "
             "down so the gripper hovers just clear of the table.",
    ),
    "hover_over_max": Pose(
        elbow=57, wrist=132, twist=90, distance=120, height=0,
        summary="Fully reaching out, gripper over the 120 mm mark.",
        hint="The furthest the gripper reaches while still hovering low. "
             "Stop before the arm strains or the base lifts.",
    ),
    "hover_min_120": Pose(
        elbow=57, wrist=40, twist=90, distance=30, height=50,
        summary="Folded in, but lifted 50 mm off the table.",
        hint="The first point the gripper can reach at the upper edge — "
             "close in, and noticeably higher than the z=0 poses.",
    ),
    "hover_mid_120": Pose(
        elbow=57, wrist=78, twist=90, distance=75, height=50,
        summary="Half extended at 50 mm, gripper over the 75 mm mark.",
        hint="Same reach as mid, raised. The forearm points out and level "
             "rather than down.",
    ),
    "hover_max_120": Pose(
        elbow=78, wrist=134, twist=90, distance=120, height=50,
        summary="Reaching out at 50 mm, gripper over the 120 mm mark.",
        hint="The far upper corner of the workspace. Furthest reach that "
             "still holds the gripper off the table.",
    ),
}

# Segment lengths, in the drawing's own units — the arm's real proportions,
# not millimetres. The table line and reach marks are scaled to match, so a
# pose that reaches "120 mm" lands on the 120 mm mark by construction.
BASE_HEIGHT = 26
UPPER_ARM = 62
FOREARM = 46
GRIPPER = 16


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
        """Show where the arm actually is. Repaints only on a real change."""
        live = (elbow, wrist, twist)
        if live != self._live:
            self._live = live
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

        area = QRectF(self.rect()).adjusted(10, 10, -10, -18)
        origin = QPointF(area.left() + area.width() * 0.22, area.bottom() - 14)
        # One unit of reach in pixels, sized so max reach uses the width that
        # is actually there rather than a fixed guess.
        scale = min(
            (area.width() * 0.72) / (UPPER_ARM + FOREARM + GRIPPER),
            (area.height() * 0.88) / (BASE_HEIGHT + UPPER_ARM + FOREARM),
        )

        self._draw_table(painter, area, origin, scale, ink, muted)
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
        painter.end()

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
            x = origin.x() + millimetres * self._mm_to_units() * scale
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
        """Drawing units per millimetre.

        Max reach (120 mm) should land at roughly the arm's full extension,
        which is what ties the reach marks to the segment lengths above.
        """
        return (UPPER_ARM + FOREARM + GRIPPER) / 150.0

    def _draw_arm(self, painter, origin, scale, elbow, wrist, pen) -> None:
        """One arm, from the two angles that decide its shape.

        Servo degrees are not screen degrees: 0-180 on the elbow sweeps the
        upper arm from leaning back to reaching forward, and the wrist angle
        is measured against the upper arm rather than the table. Mapping them
        here is what makes the drawing move the way the real arm does.
        """
        painter.setPen(pen)

        base_top = QPointF(origin.x(), origin.y() - BASE_HEIGHT * scale)
        painter.drawLine(origin, base_top)

        # Screen angles are measured from straight up, swinging forward over
        # the table. The elbow servo is taken as that same angle directly: 0
        # stands the upper arm up, 180 lays it flat forward.
        #
        # This is a drawing convention, not a claim about the servo horns —
        # which is the honest position to take, because how a given robot's
        # horns are mounted is precisely what this calibration measures and
        # what Studio therefore cannot know in advance.
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

        # The gripper carries on in the forearm's direction, drawn as an open
        # jaw so the drawing has an obvious "this end holds things" tip.
        spread = math.radians(14)
        for offset in (spread, -spread):
            jaw = fore + offset
            painter.drawLine(
                hand,
                QPointF(
                    hand.x() + math.sin(jaw) * GRIPPER * scale,
                    hand.y() - math.cos(jaw) * GRIPPER * scale,
                ),
            )

        # A dot at the base so the arm is visibly anchored rather than
        # floating at whatever angle it happens to be.
        path = QPainterPath()
        path.addEllipse(origin, 3.0, 3.0)
        painter.fillPath(path, pen.color())

