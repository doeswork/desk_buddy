"""What the robot is doing, beside the workflow that is doing it.

A run used to be a list of words — "1. detect_object — done" — which says a
step finished but nothing about what the robot saw or where its arm went. When
a detection finds nothing, that list gives the user no way to tell a bad
prompt from a camera pointed at the ceiling.

So this shows the two things a person would look at if they were standing
over the machine:

    the arm      drawn from the heartbeat, moving as the real arm moves
    the frame    the last photo this run took, with the detector's boxes on it

The photo is the important half. Every `detect_object` publishes the exact
image the detector judged, so a run that found nothing can be read rather than
guessed at: the eraser out of frame, the arm shadowing it, or a box drawn
around the wrong thing entirely.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from ....services.vision.frames import decode_frame
from ...theme.metrics import CARD_SPACING
from ..calibration.ik.arm_pose import POSES, ArmPoseView

# The pose the drawing falls back to before a heartbeat arrives. Any of the
# six would do — it is a shape to hang the segments on, replaced by the real
# angles the moment the robot reports in.
RESTING = POSES["hover_over_mid"]


class PhotoView(QWidget):
    """The last frame this run captured, with the detector's boxes on it."""

    def __init__(self) -> None:
        super().__init__()
        self._image = QImage()
        self._detections: list[dict] = []
        self._caption = "No photo yet."
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_photo(self, jpeg: bytes) -> None:
        # A new frame is a new judgement, whether or not it could be read:
        # the boxes belong to the photo they were drawn for, and keeping them
        # would paint the last detection over this one — or over nothing.
        self._detections = []
        image = QImage()
        if jpeg and image.loadFromData(jpeg, "JPEG"):
            self._image = image
            self._caption = ""
        else:
            self._image = QImage()
            self._caption = "The frame could not be read."
        self.update()

    def set_detections(self, detections: list[dict], caption: str = "") -> None:
        self._detections = list(detections)
        self._caption = caption
        self.update()

    # ---- drawing ---------------------------------------------------------
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        colors = self.palette()
        ink = colors.windowText().color()

        if self._image.isNull():
            painter.setPen(QPen(ink))
            painter.drawText(self.rect(), Qt.AlignCenter, self._caption or "No photo yet.")
            painter.end()
            return

        # Fitted, not stretched: a squashed frame is a frame whose boxes no
        # longer sit over what they found.
        scaled = self._image.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        x = (self.width() - scaled.width()) / 2
        y = (self.height() - scaled.height()) / 2
        painter.drawPixmap(int(x), int(y), QPixmap.fromImage(scaled))

        # Boxes come back normalised, so they survive that scaling unchanged.
        accent = colors.highlight().color()
        painter.setFont(self._label_font())
        for detection in self._detections:
            box = detection.get("box_normalized")
            if not (isinstance(box, (list, tuple)) and len(box) == 4):
                continue
            left, top, right, bottom = (float(value) for value in box)
            rect = QRectF(
                x + left * scaled.width(),
                y + top * scaled.height(),
                (right - left) * scaled.width(),
                (bottom - top) * scaled.height(),
            )
            painter.setPen(QPen(accent, 2.0))
            painter.drawRect(rect)

            label = str(detection.get("label") or "")
            score = detection.get("score")
            if isinstance(score, (int, float)):
                label = f"{label} {score:.2f}".strip()
            if label:
                self._draw_label(painter, rect, label, accent, colors)

        painter.end()

    def _label_font(self) -> QFont:
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1))
        return font

    def _draw_label(self, painter, rect, text, accent, colors) -> None:
        """The score, on a filled tab so it stays readable over any photo."""
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 8
        height = metrics.height() + 2
        # Above the box, or inside it when the box is already at the top.
        top = rect.top() - height
        if top < 0:
            top = rect.top()
        tab = QRectF(rect.left(), top, width, height)

        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        painter.drawRect(tab)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(colors.base().color()))
        painter.drawText(tab, Qt.AlignCenter, text)


class LiveView(QWidget):
    """The arm and the last frame, stacked down one column."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("WorkflowLiveView")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(CARD_SPACING)

        # The arm keeps its natural height; the photo takes the rest. A
        # drawing given room to stretch is mostly blank table, and the frame
        # is the half worth looking at.
        self.arm = ArmPoseView(RESTING)
        self.arm.setMinimumHeight(150)
        self.arm.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.arm)

        self.photo = PhotoView()
        layout.addWidget(self.photo, 1)

        self.caption = QLabel("Waiting for the robot.")
        self.caption.setObjectName("CardBody")
        self.caption.setWordWrap(True)
        layout.addWidget(self.caption)

    # ---- what the runner feeds it ----------------------------------------
    def set_heartbeat(self, heartbeat: dict, frame) -> None:
        """Move the drawn arm to wherever the real one is.

        `frame` maps this robot's servo degrees into the drawing's, the same
        way the IK page does. Without one there is no way to place a servo
        angle on the drawing, so the live arm is left off rather than drawn
        somewhere invented.
        """
        elbow = heartbeat.get("ELBOW_ANGLE")
        wrist = heartbeat.get("WRIST_ANGLE")
        if elbow is None or wrist is None:
            return
        on_screen = frame.to_screen(elbow, wrist) if frame is not None else None
        if on_screen is None:
            self.arm.clear_live()
        else:
            self.arm.set_live(on_screen[0], on_screen[1], 90)

    def set_photo(self, payload: bytes) -> None:
        """One binary photo frame, straight off `{robot}/photos`."""
        try:
            frame = decode_frame(payload)
        except Exception:
            self.photo.set_photo(b"")
            return
        self.photo.set_photo(frame.jpeg)

    def set_vision(self, result: dict) -> None:
        """The detector's verdict, drawn over the frame it judged."""
        batch = result.get("detection_batch") or {}
        detections = batch.get("detections")
        detections = detections if isinstance(detections, list) else []
        prompt = batch.get("prompt") or ""

        if result.get("status") == "failed":
            message = (result.get("error") or {}).get("message") or "No detection."
            self.photo.set_detections(detections, message)
            self.caption.setText(message)
            return

        found = f"Found {len(detections)} × {prompt!r}" if prompt else f"Found {len(detections)}"
        self.photo.set_detections(detections, found)
        self.caption.setText(found)

    def set_caption(self, text: str) -> None:
        self.caption.setText(text)
