"""Capture previews and operation details for the Vision page."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ...components import Card
from .widgets import depth_crop_pixmap


def preview_card(controller, latest: dict) -> QWidget:
    card = Card("Latest capture", muted=not bool(latest))
    row = QHBoxLayout()
    source = QLabel("Source image and boxes")
    source.setAlignment(Qt.AlignCenter)
    source.setMinimumHeight(180)
    row.addWidget(source, 1)

    context = controller.latest() if controller is not None else None
    if context is not None:
        source_pixmap = QPixmap()
        source_pixmap.loadFromData(context.jpeg, "JPEG")
        detection = context.selected_detection or (context.observation.detection if context.observation else None)
        if detection is not None:
            painter = QPainter(source_pixmap)
            painter.setPen(QPen(QColor("#ffcc33"), 3))
            x0, y0, x1, y1 = detection.bbox_px
            painter.drawRect(int(x0), int(y0), max(1, int(x1 - x0)), max(1, int(y1 - y0)))
            painter.end()
        source.setPixmap(source_pixmap.scaled(280, 190, Qt.KeepAspectRatio, Qt.SmoothTransformation))

        depth_job = context.jobs.get("depth.infer", "")
        if depth_job:
            depth = QLabel("Depth preview")
            crop = QLabel("64×64 crop")
            for label in (depth, crop):
                label.setAlignment(Qt.AlignCenter)
                label.setMinimumHeight(180)
                row.addWidget(label, 1)
            preview = context.artifacts.get(depth_job, {}).get("depth_preview")
            if preview is not None:
                depth_pixmap = QPixmap()
                depth_pixmap.loadFromData(preview.payload, "PNG")
                depth.setPixmap(depth_pixmap.scaled(280, 190, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            if context.observation is not None:
                crop.setPixmap(
                    depth_crop_pixmap(context.observation.depth_patch_64x64).scaled(
                        190, 190, Qt.KeepAspectRatio, Qt.FastTransformation
                    )
                )

    card.layout().addLayout(row)
    return card


def operation_card(latest: dict) -> QWidget:
    if not latest:
        return Card("Operation details", "No capture has been submitted.")

    observation = latest.get("observation")
    detection = latest.get("selected_detection") or (observation.get("detection") if observation else None)
    selected = "No selected detection"
    if detection:
        center = tuple(round(value, 3) for value in detection["center_norm"])
        selected = f"{detection['label']} ({detection['score']:.3f}) · center {center}"

    target_lines = []
    for label, target in (
        ("Deterministic", latest.get("deterministic_target")),
        ("Learned", latest.get("learned_target")),
    ):
        if target:
            target_lines.append(
                f"{label}: rotation {target['rotation_deg']:.2f}° · distance {target['distance_mm']:.2f} mm · "
                f"z {target['z_height_mm']:.2f} mm · {'accepted' if target['accepted'] else 'rejected'}"
            )
    target_text = "\n".join(target_lines) or "No IK target"
    timings = ", ".join(
        f"{kind}: {value:.1f} ms" for kind, value in latest.get("timings_ms", {}).items()
    ) or "pending"
    deterministic = latest.get("deterministic_target") or {}
    return Card(
        f"State: {latest.get('state', 'unknown')}",
        f"Selected: {selected}\n{target_text}\nWorkspace: "
        f"{'covered' if deterministic.get('accepted') else 'outside/not calibrated'}\n"
        f"Timing: {timings}\nError: {latest.get('error') or 'none'}",
        muted=False,
    )
