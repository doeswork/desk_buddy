"""Offscreen coverage for the Vision model picker and preview UI.

    QT_QPA_PLATFORM=offscreen python -m studio.ui.workspaces.vision.tests
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
)

from ....services.vision.catalog import BUILTIN_MODELS, DEFAULT_MODEL_ID
from ....services.vision.tests import mark_installed, temporary_manager
from .detections import DetectionOverlay
from .workspace import VisionWorkspace

_app = QApplication.instance() or QApplication([])


def jpeg(width: int = 20, height: int = 10) -> bytes:
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor("#5a84a2"))
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    assert buffer.open(QIODevice.WriteOnly)
    assert image.save(buffer, "JPEG", 90)
    return bytes(encoded)


def button(widget, label: str) -> QPushButton:
    return next(item for item in widget.findChildren(QPushButton) if item.text() == label)


def test_picker_is_visible_before_install_with_both_curated_models() -> None:
    with temporary_manager() as (manager, _client):
        workspace = VisionWorkspace(manager=manager)
        widget = workspace.widget()
        picker = widget.findChild(QComboBox, "VisionModelPicker")
        assert picker is not None
        assert picker.count() == 2
        assert [picker.itemData(index) for index in range(picker.count())] == [
            item.model_id for item in BUILTIN_MODELS
        ]
        assert picker.currentData() == DEFAULT_MODEL_ID
        assert button(widget, "Install & Use").isEnabled()
        auto = next(item for item in widget.findChildren(QCheckBox) if item.text() == "Start with Studio")
        assert not auto.isChecked()


def test_primary_action_labels_follow_install_and_active_state() -> None:
    with temporary_manager() as (manager, _client):
        mark_installed(manager, DEFAULT_MODEL_ID)
        workspace = VisionWorkspace(manager=manager)
        workspace.widget()
        assert workspace.primary_action_label == "Use & Start"
        manager._replace(active_model_id=DEFAULT_MODEL_ID)
        assert workspace.primary_action_label == "Start"
        manager._replace(process_state="running", mqtt_state="ready", device="cpu")
        assert workspace.primary_action_label == "Running"
        assert workspace.primary_action is None


def test_install_progress_and_error_are_rendered_in_page() -> None:
    with temporary_manager() as (manager, _client):
        workspace = VisionWorkspace(manager=manager)
        widget = workspace.widget()
        manager._replace(
            operation="install", phase="model", progress_message="Downloading pinned snapshot…",
            progress_current=25, progress_total=100, error="network interrupted",
        )
        progress = widget.findChild(QProgressBar, "VisionProgress")
        assert progress is not None
        assert (progress.value(), progress.maximum()) == (25, 100)
        texts = [label.text() for label in widget.findChildren(QPushButton)]
        assert "Cancel" in texts
        labels = widget.findChildren(QLabel)
        error = next(label for label in labels if "network interrupted" in label.text())
        assert error.textInteractionFlags() & Qt.TextSelectableByMouse


def test_detection_page_offers_robot_and_local_sources() -> None:
    with temporary_manager() as (manager, _client):
        workspace = VisionWorkspace(manager=manager)
        widget = workspace.widget()
        workspace.go_to("detections")
        picker = widget.findChild(QComboBox, "VisionSourcePicker")
        assert [picker.itemText(index) for index in range(picker.count())] == [
            "Robot camera", "Local photo",
        ]
        actions = [item for item in workspace.build_actions() if hasattr(item, "label")]
        test_photo = next(item for item in actions if item.label == "Test Photo")
        workspace.go_to("models")
        test_photo.on_click()
        assert workspace.page_key == "detections"


def test_local_preview_sends_jpeg_bytes_and_keeps_result_in_memory() -> None:
    with temporary_manager() as (manager, client):
        manager._replace(
            active_model_id=DEFAULT_MODEL_ID, process_state="running", mqtt_state="ready",
        )
        workspace = VisionWorkspace(manager=manager)
        widget = workspace.widget()
        workspace.go_to("detections")
        page = workspace.find("detections")
        page.source_changed("Local photo")
        page._local_jpeg = jpeg()
        page._local_name = "desk.jpg"
        page._image = page._local_jpeg
        page.rebuild()
        preview = button(widget, "Preview Detection")
        prompt = widget.findChild(QLineEdit, "VisionPrompt")
        assert not preview.isEnabled()
        prompt.setText("red mug")
        assert preview.isEnabled()
        preview.click()
        assert client.raw_sent and client.raw_sent[-1][1] != b"desk.jpg"
        assert page._result is None

        selected = {"label": "mug", "score": 0.91, "box_px": [2, 1, 12, 9]}
        page._on_result({
            "status": "completed", "model_id": BUILTIN_MODELS[0].source,
            "revision": BUILTIN_MODELS[0].revision,
            "detection_batch": {"detections": [selected]},
            "selected_detection": selected,
        })
        overlay = widget.findChild(DetectionOverlay)
        assert overlay is not None
        assert overlay.selected == selected
        assert page._image == page._local_jpeg


def test_overlay_letterboxes_and_scales_pixel_boxes() -> None:
    image = jpeg(20, 10)
    overlay = DetectionOverlay(image, (), None)
    overlay.resize(200, 200)
    # Its 320px minimum height produces a 200x100 image centered vertically.
    assert overlay.image_target_rect().getRect() == (0, 110, 200, 100)
    assert overlay.mapped_box([0, 0, 10, 10]).getRect() == (0, 110, 100, 100)


def test_robot_failure_without_a_decodable_photo_is_visible() -> None:
    with temporary_manager() as (manager, _client):
        workspace = VisionWorkspace(manager=manager)
        widget = workspace.widget()
        workspace.go_to("detections")
        page = workspace.find("detections")
        page._on_result({
            "status": "failed",
            "error": {"code": "invalid_image", "message": "Robot photo was malformed."},
        })
        labels = [label.text() for label in widget.findChildren(QLabel)]
        assert "Detection failed" in labels
        assert "Robot photo was malformed." in labels


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} vision workspace tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
