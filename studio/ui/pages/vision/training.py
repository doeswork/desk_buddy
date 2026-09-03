"""Review and custom-model controls for the Vision page."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from ...components import Card
from .widgets import number_input


@dataclass(frozen=True)
class TrainingCallbacks:
    test_pose: Callable[[float, float, float], None]
    review: Callable[[str, float, float, float], None]
    train: Callable[[str], None]
    load: Callable[[], None]
    activate: Callable[[], None]


def review_card(latest: Mapping, connected: bool, callbacks: TrainingCallbacks) -> QWidget:
    card = Card("Review queue", "Adjust the final supervised pose, then record the outcome.", muted=False)
    target = latest.get("learned_target") or latest.get("deterministic_target") or {}
    row = QHBoxLayout()
    rotation = number_input(-45, 45, float(target.get("rotation_deg", 0)))
    distance = number_input(0, 180, float(target.get("distance_mm", 0)))
    height = number_input(-30, 120, float(target.get("z_height_mm", 0)))
    for label, widget in (("Rotation °", rotation), ("Distance mm", distance), ("Z mm", height)):
        row.addWidget(QLabel(label))
        row.addWidget(widget)

    capture_available = bool(latest.get("capture_id"))
    test_pose = QPushButton("Test Pose")
    test_pose.setEnabled(capture_available and connected)
    test_pose.clicked.connect(lambda: callbacks.test_pose(rotation.value(), distance.value(), height.value()))
    row.addWidget(test_pose)
    for text, disposition in (("Successful", "successful"), ("Failed", "failed"), ("Discard", "discarded")):
        button = QPushButton(text)
        button.setEnabled(capture_available)
        button.clicked.connect(
            lambda checked=False, value=disposition: callbacks.review(
                value, rotation.value(), distance.value(), height.value()
            )
        )
        row.addWidget(button)
    card.layout().addLayout(row)
    return card


def model_builder_card(
    *,
    examples: int,
    models: Iterable[Mapping],
    connected: bool,
    selected_model_id: str,
    callbacks: TrainingCallbacks,
) -> QWidget:
    records = tuple(models)
    card = Card(
        "Model Builder — Preview",
        f"{examples} reviewed successful capture(s) · {len(records)} trained model(s). "
        "Composed detector/depth/MLP pipelines are not enabled in this release.",
        muted=False,
    )
    row = QHBoxLayout()
    name = QLineEdit("desk-buddy-ik")
    train = QPushButton("Train Reviewed")
    train.setEnabled(False)
    train.setToolTip("Training is disabled until the composed MLP input pipeline is enabled.")
    row.addWidget(name, 1)
    row.addWidget(train)
    card.layout().addLayout(row)

    if records:
        lines = []
        for model in records:
            metrics = model["metadata"].get("metrics", {})
            lines.append(
                f"{model['model_id']} — {'active' if model['active'] else 'shadow'} · MAE r/d/z "
                f"{metrics.get('validation_mae_rotation_deg', '?')} / "
                f"{metrics.get('validation_mae_distance_mm', '?')} / "
                f"{metrics.get('validation_mae_z_height_mm', '?')}"
            )
        detail = QLabel("\n".join(lines))
        detail.setWordWrap(True)
        card.layout().addWidget(detail)
    return card


def trainer_runtime_card(
    state: Mapping,
    *,
    install: Callable[[str, bool], None],
    start: Callable[[str], None],
    stop: Callable[[str], None],
    restart: Callable[[str], None],
    health: Callable[[str, str], None],
    reset: Callable[[str], None],
    autostart: Callable[[str, bool], None],
) -> QWidget:
    card = Card(
        "MLP training worker — Preview",
        "The isolated trainer runtime can be prepared now, but training jobs remain disabled.",
        muted=False,
    )
    row = QHBoxLayout()
    installed = bool(state.get("runtime_installed"))
    running = state.get("process_state") in {"starting", "running"}
    setup = QPushButton("Set Up Runtime" if not installed else "Repair Runtime")
    setup.clicked.connect(lambda: install("trainer", installed))
    row.addWidget(setup)
    start_button = QPushButton("Start")
    start_button.setEnabled(installed and not running)
    start_button.clicked.connect(lambda: start("trainer"))
    row.addWidget(start_button)
    stop_button = QPushButton("Stop")
    stop_button.setEnabled(running)
    stop_button.clicked.connect(lambda: stop("trainer"))
    row.addWidget(stop_button)
    restart_button = QPushButton("Restart")
    restart_button.setEnabled(running)
    restart_button.clicked.connect(lambda: restart("trainer"))
    row.addWidget(restart_button)
    health_button = QPushButton("Health Check")
    health_button.setEnabled(state.get("mqtt_state") == "ready")
    health_button.clicked.connect(lambda: health("ik-trainer-1", "ik-mlp-builder"))
    row.addWidget(health_button)
    reset_button = QPushButton("Reset Runtime")
    reset_button.setEnabled(installed and not running)
    reset_button.clicked.connect(lambda: reset("trainer"))
    row.addWidget(reset_button)
    card.layout().addLayout(row)
    toggle = QCheckBox("Start with Studio")
    toggle.setChecked(bool(state.get("start_with_studio")))
    toggle.toggled.connect(lambda checked: autostart("trainer", checked))
    card.layout().addWidget(toggle)
    detail = QLabel(
        f"Runtime: {'installed' if installed else 'not installed'} · "
        f"Process: {state.get('process_state', 'stopped')} · MQTT: {state.get('mqtt_state', 'offline')}"
    )
    detail.setWordWrap(True)
    card.layout().addWidget(detail)
    return card
