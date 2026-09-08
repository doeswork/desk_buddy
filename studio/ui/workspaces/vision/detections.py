"""Safe preview inference from a local image or the robot camera."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....models.config.robots import robots
from ...components import Card, Column
from ...pages.base import Page
from ...theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING


class DetectionsPage(Page):
    key = "detections"
    label = "Detections"
    title = "Detection Preview"
    subtitle = "Find an object in a local photo or a fresh robot capture. Preview never moves the robot."

    def __init__(self, workspace=None) -> None:
        super().__init__(workspace)
        self._source = "robot"
        self._prompt = ""
        self._robot = ""
        self._local_jpeg = b""
        self._local_name = ""
        self._image = b""
        self._result: dict | None = None
        self.workspace.manager.photo_received.connect(self._on_photo)
        self.workspace.manager.detection_result.connect(self._on_result)

    @property
    def status(self) -> str:
        state = self.workspace.manager.state
        if state.pending_detection:
            return "Detection in progress"
        if self._result and self._result.get("status") == "completed":
            return "Detection preview complete"
        if self._result and self._result.get("status") == "failed":
            return "Detection preview failed"
        return "Preview mode · no motion"

    @property
    def selected_robot(self) -> str:
        if self._robot and robots().find(self._robot):
            return self._robot
        available = robots().all()
        return available[0].name if available else ""

    def build_page(self) -> QWidget:
        sections: list[QWidget] = [DetectionForm(self)]
        if self._image:
            sections.append(PreviewCard(self._image, self._result))
        elif self._result and self._result.get("status") == "failed":
            error = self._result.get("error") or {}
            sections.append(Card(
                "Detection failed",
                str(error.get("message") or "The detector rejected the preview request."),
            ))
        else:
            sections.append(Card(
                "No photo selected",
                "Choose a local image or select a configured robot, then run Preview Detection.",
            ))
        state = self.workspace.manager.state
        if state.error:
            sections.append(Card("Detection unavailable", state.error))
        return Column(*sections)

    def choose_photo(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self.widget(), "Choose a photo", "",
            "Images (*.jpg *.jpeg *.png *.webp *.bmp);;All files (*)",
        )
        if not path:
            return
        try:
            source = Path(path).read_bytes()
        except OSError as exc:
            self.workspace.manager.report_error(f"Could not read that photo: {exc}")
            return
        image = QImage.fromData(source)
        if image.isNull():
            self.workspace.manager.report_error("That file is not a supported image.")
            return
        encoded = QByteArray()
        buffer = QBuffer(encoded)
        if not buffer.open(QIODevice.WriteOnly) or not image.save(buffer, "JPEG", 90):
            self.workspace.manager.report_error("Qt could not convert that image to JPEG.")
            return
        self._local_jpeg = bytes(encoded)
        self._local_name = Path(path).name
        self._image = self._local_jpeg
        self._result = None
        self.rebuild()

    def detect(self) -> None:
        self._result = None
        if self._source == "local":
            if not self._local_jpeg:
                self.workspace.manager.report_error("Choose a local photo first.")
                return
            self.workspace.manager.detect_local(self._local_jpeg, self._prompt)
        else:
            self.workspace.manager.detect_robot(self.selected_robot, self._prompt)

    def source_changed(self, source: str) -> None:
        value = "local" if source == "Local photo" else "robot"
        if value != self._source:
            self._source = value
            self._result = None
            self._image = self._local_jpeg if value == "local" else b""
            self.rebuild()

    def prompt_changed(self, prompt: str) -> None:
        self._prompt = prompt

    def robot_changed(self, robot: str) -> None:
        self._robot = robot

    def _on_photo(self, jpeg: bytes) -> None:
        self._image = bytes(jpeg)
        self.rebuild()

    def _on_result(self, result: dict) -> None:
        self._result = dict(result)
        self.rebuild()


class DetectionForm(QFrame):
    def __init__(self, page: DetectionsPage) -> None:
        super().__init__()
        self.setObjectName("Card")
        manager = page.workspace.manager
        state = manager.state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, CARD_MARGIN_V)
        layout.setSpacing(CARD_SPACING * 2)

        title = QLabel("Preview input")
        title.setObjectName("CardTitle")
        layout.addWidget(title)

        source_row = QHBoxLayout()
        source_label = QLabel("Image source")
        source_label.setObjectName("CardBody")
        source_row.addWidget(source_label)
        source = QComboBox()
        source.setObjectName("VisionSourcePicker")
        source.addItems(("Robot camera", "Local photo"))
        source.setCurrentIndex(1 if page._source == "local" else 0)
        source.currentTextChanged.connect(page.source_changed)
        source_row.addWidget(source, 1)
        layout.addLayout(source_row)

        if page._source == "robot":
            row = QHBoxLayout()
            label = QLabel("Robot")
            label.setObjectName("CardBody")
            row.addWidget(label)
            picker = QComboBox()
            picker.setObjectName("VisionRobotPicker")
            available = robots().all()
            for robot in available:
                picker.addItem(robot.display_name, robot.name)
            if page.selected_robot:
                picker.setCurrentIndex(max(0, picker.findData(page.selected_robot)))
            picker.setEnabled(bool(available) and not state.pending_detection)
            picker.currentIndexChanged.connect(
                lambda index: page.robot_changed(str(picker.itemData(index))) if index >= 0 else None
            )
            row.addWidget(picker, 1)
            if not available:
                hint = QLabel("Mark a user as a robot on Network → Robots first.")
                hint.setObjectName("FieldError")
                row.addWidget(hint)
            layout.addLayout(row)
        else:
            row = QHBoxLayout()
            selected = QLabel(page._local_name or "No local photo chosen")
            selected.setObjectName("CardBody")
            row.addWidget(selected, 1)
            choose = QPushButton("Choose Photo…")
            choose.setObjectName("ContextAction")
            choose.setEnabled(not state.pending_detection)
            choose.clicked.connect(page.choose_photo)
            row.addWidget(choose)
            layout.addLayout(row)

        prompt_row = QHBoxLayout()
        prompt = QLineEdit(page._prompt)
        prompt.setObjectName("VisionPrompt")
        prompt.setPlaceholderText("What should the detector find? e.g. red mug")
        prompt.setEnabled(not state.pending_detection)
        prompt.returnPressed.connect(page.detect)
        prompt_row.addWidget(prompt, 1)
        detect = QPushButton("Preview Detection")
        detect.setObjectName("ContextPrimary")
        source_ready = bool(page._local_jpeg) if page._source == "local" else bool(page.selected_robot)

        def update_prompt(value: str) -> None:
            page.prompt_changed(value)
            current = manager.state
            detect.setEnabled(
                current.ready and source_ready and bool(value.strip())
                and not current.pending_detection and not current.operation
            )

        update_prompt(page._prompt)
        prompt.textChanged.connect(update_prompt)
        detect.clicked.connect(page.detect)
        prompt_row.addWidget(detect)
        layout.addLayout(prompt_row)

        if not state.ready:
            hint = QLabel("Install and start a detector on Models before running a preview.")
            hint.setObjectName("CardBody")
            layout.addWidget(hint)


class PreviewCard(QFrame):
    def __init__(self, jpeg: bytes, result: dict | None) -> None:
        super().__init__()
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, CARD_MARGIN_V)
        layout.setSpacing(CARD_SPACING * 2)
        title = QLabel("Detection result")
        title.setObjectName("CardTitle")
        layout.addWidget(title)
        batch = result.get("detection_batch", {}) if result else {}
        selected = result.get("selected_detection") if result else None
        view = DetectionOverlay(jpeg, batch.get("detections", ()), selected)
        layout.addWidget(view)
        if result is None:
            text = "Photo ready; waiting for detector output…"
        elif result.get("status") == "failed":
            error = result.get("error") or {}
            text = str(error.get("message") or "Detection failed.")
        else:
            score = float((selected or {}).get("score", 0))
            label = str((selected or {}).get("label") or "object")
            text = (
                f"Selected {label} · {score:.1%} confidence\n"
                f"{result.get('model_id')}@{result.get('revision')}"
            )
        summary = QLabel(text)
        summary.setObjectName("CardBody" if not result or result.get("status") == "completed" else "FieldError")
        summary.setWordWrap(True)
        summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(summary)


class DetectionOverlay(QWidget):
    """Scale an image to fit and paint model boxes in image coordinates."""

    def __init__(self, jpeg: bytes, detections, selected) -> None:
        super().__init__()
        self.image = QImage.fromData(jpeg)
        self.detections = tuple(detections or ())
        self.selected = selected
        self.setMinimumHeight(320)

    def sizeHint(self) -> QSize:
        return QSize(720, 440)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101820"))
        if self.image.isNull():
            painter.end()
            return
        target = self.image_target_rect()
        painter.drawImage(target, self.image)
        for detection in self.detections:
            box = detection.get("box_px", ())
            if len(box) != 4:
                continue
            chosen = detection == self.selected
            color = QColor("#ffb000" if chosen else "#38d996")
            painter.setPen(QPen(color, 4 if chosen else 2))
            rectangle = self.mapped_box(box)
            painter.drawRect(rectangle)
            caption = f"{detection.get('label', 'object')} {float(detection.get('score', 0)):.0%}"
            metrics = QFontMetrics(painter.font())
            label_rect = metrics.boundingRect(caption).adjusted(-4, -2, 4, 2)
            label_rect.moveBottomLeft(rectangle.topLeft())
            if label_rect.top() < target.top():
                label_rect.moveTopLeft(rectangle.topLeft())
            painter.fillRect(label_rect, color)
            painter.setPen(QColor("#101820"))
            painter.drawText(label_rect, Qt.AlignCenter, caption)
        painter.end()

    def image_target_rect(self) -> QRect:
        if self.image.isNull():
            return QRect()
        fitted = self.image.size().scaled(self.size(), Qt.KeepAspectRatio)
        return QRect(
            (self.width() - fitted.width()) // 2,
            (self.height() - fitted.height()) // 2,
            fitted.width(), fitted.height(),
        )

    def mapped_box(self, box) -> QRect:
        """Map one pixel-space model box into the letterboxed widget."""
        target = self.image_target_rect()
        if self.image.isNull() or len(box) != 4:
            return QRect()
        scale_x = target.width() / self.image.width()
        scale_y = target.height() / self.image.height()
        x0, y0, x1, y1 = [float(value) for value in box]
        return QRect(
            round(target.left() + x0 * scale_x),
            round(target.top() + y0 * scale_y),
            max(1, round((x1 - x0) * scale_x)),
            max(1, round((y1 - y0) * scale_y)),
        )
