"""Deterministic capture and advanced external-worker controls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtWidgets import QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from ...components import Card
from .state import ProviderOption, VisionPageState


@dataclass(frozen=True)
class PipelineCallbacks:
    start_worker: Callable[[str], None]
    stop_worker: Callable[[str], None]
    install_worker: Callable[[str], None]


def capture_card(
    *,
    state: VisionPageState,
    detector: ProviderOption | None,
    prompt_changed: Callable[[str], None],
) -> QWidget:
    ready = bool(detector and detector.ready)
    card = Card(
        "Nine-point deterministic IK",
        "This strategy requires zero-shot detection only. Depth and Custom MLP services are not invoked.",
        muted=False,
    )
    form = QFormLayout()
    prompt = QLineEdit(state.prompt)
    prompt.textChanged.connect(prompt_changed)
    form.addRow("Object prompt", prompt)
    strategy = QComboBox()
    strategy.addItem("Nine-point deterministic", "deterministic")
    form.addRow("IK strategy", strategy)
    provider = QLabel(detector.label if detector else "No detector selected")
    form.addRow("Detector", provider)
    form.addRow("Requirements", QLabel("Detection — ready" if ready else "Detection — offline"))
    card.layout().addLayout(form)
    return card


def worker_controls_card(launcher, callbacks: PipelineCallbacks) -> QWidget:
    if not launcher.workers:
        return Card(
            "Worker deployment",
            "Remote workers appear through retained MQTT status. Configure DESK_BUDDY_VISION_LOCAL_WORKERS to add local start/stop controls.",
        )
    card = Card(
        "Local workers",
        "These controls only launch processes; requests and artifacts still travel through MQTT.",
        muted=False,
    )
    row = QHBoxLayout()
    for worker_id, worker in launcher.workers.items():
        start, stop = QPushButton(f"Start {worker_id}"), QPushButton(f"Stop {worker_id}")
        start.clicked.connect(lambda checked=False, value=worker_id: callbacks.start_worker(value))
        stop.clicked.connect(lambda checked=False, value=worker_id: callbacks.stop_worker(value))
        row.addWidget(start)
        row.addWidget(stop)
        if worker.install_command:
            install = QPushButton(f"Install {worker_id} model")
            install.clicked.connect(lambda checked=False, value=worker_id: callbacks.install_worker(value))
            row.addWidget(install)
    card.layout().addLayout(row)
    return card
