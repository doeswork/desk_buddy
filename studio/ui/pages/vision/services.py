"""Managed Detection, Depth, and Custom MLP service tabs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...components import Card, Column
from ....services.vision.access import ManagedCredentialState, VisionAccessIdentity
from .access_widgets import AccessCallbacks, service_access_card
from .state import ProviderOption


@dataclass(frozen=True)
class ServiceCallbacks:
    candidate_changed: Callable[[str, ProviderOption], None]
    install: Callable[[str, bool], None]
    use_start: Callable[[str, ProviderOption], None]
    start: Callable[[str], None]
    stop: Callable[[str], None]
    restart: Callable[[str], None]
    health: Callable[[str, str], None]
    remove_download: Callable[[str], None]
    reset_runtime: Callable[[str], None]
    cancel_install: Callable[[], None]
    autostart_changed: Callable[[str, bool], None]
    import_manifest: Callable[[str], None]


def services_tabs(
    *,
    detector_options: tuple[ProviderOption, ...],
    depth_options: tuple[ProviderOption, ...],
    detector_candidate: tuple[str, str],
    depth_candidate: tuple[str, str],
    managed_states: Mapping[str, Mapping],
    manifests: Mapping[tuple[str, str], object],
    learned_models: Iterable[Mapping],
    mlp_candidate: str,
    external_workers: QWidget,
    callbacks: ServiceCallbacks,
    access_callbacks: AccessCallbacks,
    detector_access: tuple[VisionAccessIdentity, ManagedCredentialState],
    depth_access: tuple[VisionAccessIdentity, ManagedCredentialState],
    mlp_access: tuple[VisionAccessIdentity, ManagedCredentialState],
    current_tab: int = 0,
    tab_changed: Callable[[int], None] | None = None,
) -> QWidget:
    outer = QWidget()
    layout = QVBoxLayout(outer)
    layout.setContentsMargins(0, 0, 0, 0)
    tabs = QTabWidget()
    tabs.setObjectName("VisionServiceFamilies")
    tabs.addTab(
        _provider_family(
            "detection", "Detection", detector_options, detector_candidate,
            managed_states.get("detection", {}), manifests, callbacks,
            detector_access, access_callbacks,
        ),
        "Detection",
    )
    tabs.addTab(
        _provider_family(
            "depth", "Depth", depth_options, depth_candidate,
            managed_states.get("depth", {}), manifests, callbacks,
            depth_access, access_callbacks,
        ),
        "Depth",
    )
    tabs.addTab(
        _mlp_family(
            tuple(learned_models), mlp_candidate, managed_states.get("mlp", {}), callbacks,
            mlp_access, access_callbacks,
        ),
        "Custom MLP",
    )
    tabs.setCurrentIndex(min(current_tab, tabs.count() - 1))
    if tab_changed is not None:
        tabs.currentChanged.connect(tab_changed)
    layout.addWidget(tabs)
    layout.addWidget(external_workers)
    return outer


def _provider_family(
    family: str,
    title: str,
    options: tuple[ProviderOption, ...],
    selected: tuple[str, str],
    state: Mapping,
    manifests: Mapping[tuple[str, str], object],
    callbacks: ServiceCallbacks,
    access: tuple[VisionAccessIdentity, ManagedCredentialState],
    access_callbacks: AccessCallbacks,
) -> QWidget:
    card = Card(f"{title} service", muted=False)
    form = QFormLayout()
    combo = QComboBox()
    selected_index = 0
    for index, option in enumerate(options):
        local = option.key in manifests
        suffix = "Managed" if local else "Remote"
        combo.addItem(f"{option.model_id} — {option.worker_id} ({suffix})", option)
        if option.key == selected:
            selected_index = index
    combo.setCurrentIndex(selected_index)
    combo.currentIndexChanged.connect(
        lambda: callbacks.candidate_changed(family, combo.currentData())
        if isinstance(combo.currentData(), ProviderOption) else None
    )
    form.addRow("Model", combo)
    option = combo.currentData() if isinstance(combo.currentData(), ProviderOption) else None
    manifest = manifests.get(option.key) if option else None
    local = manifest is not None
    installed = bool(state.get("installed")) if local else False
    process_state = str(state.get("process_state") or "stopped")
    mqtt_state = "Ready" if option and option.ready else "Offline"
    active_model = str(state.get("active_model_id") or "None")
    active_worker = str(state.get("active_worker_id") or "")
    if active_worker:
        active_model += f" — {active_worker}"
    form.addRow("Active route", QLabel(active_model))
    form.addRow("Install", QLabel("Installed" if installed else "Not downloaded" if local else "Remote managed"))
    form.addRow("Process", QLabel(process_state.title() if local else "Remote"))
    form.addRow("MQTT", QLabel(mqtt_state))
    if manifest is not None:
        form.addRow("Source", QLabel(str(manifest.source)))
        form.addRow("Revision", QLabel(str(manifest.revision)))
        form.addRow("License", QLabel(str(manifest.license)))
        form.addRow("Size", QLabel(f"{manifest.size_mb} MB" if manifest.size_mb else "Unknown"))
        form.addRow("Devices", QLabel(", ".join(manifest.devices)))
        form.addRow("Adapter", QLabel(str(manifest.adapter_id)))
        form.addRow("Dependencies", QLabel(str(manifest.dependency_profile)))
        form.addRow("Schemas", QLabel(f"{manifest.input_schema} → {manifest.output_schema}"))
    card.layout().addLayout(form)

    toggle = QCheckBox("Start with Studio")
    toggle.setChecked(bool(state.get("start_with_studio")))
    toggle.setEnabled(local)
    toggle.toggled.connect(lambda checked: callbacks.autostart_changed(family, checked))
    card.layout().addWidget(toggle)

    row = QHBoxLayout()
    operation = str(state.get("operation") or "")
    running = process_state in {"starting", "running"}
    if local and operation:
        cancel = QPushButton("Cancel Install")
        cancel.clicked.connect(callbacks.cancel_install)
        row.addWidget(cancel)
    elif local:
        download = QPushButton("Download" if not installed else "Repair")
        download.setEnabled(not running)
        download.clicked.connect(lambda: callbacks.install(family, installed))
        row.addWidget(download)
        use = QPushButton("Use & Start")
        use.setEnabled(installed and not running)
        use.clicked.connect(lambda: callbacks.use_start(family, option) if option else None)
        row.addWidget(use)
        stop = QPushButton("Stop")
        stop.setEnabled(running)
        stop.clicked.connect(lambda: callbacks.stop(family))
        row.addWidget(stop)
        restart = QPushButton("Restart")
        restart.setEnabled(running)
        restart.clicked.connect(lambda: callbacks.restart(family))
        row.addWidget(restart)
        remove = QPushButton("Remove Download")
        remove.setEnabled(installed and not running)
        remove.clicked.connect(lambda: callbacks.remove_download(family))
        row.addWidget(remove)
        reset = QPushButton("Reset Runtime")
        reset.setEnabled(bool(state.get("runtime_installed")) and not running)
        reset.clicked.connect(lambda: callbacks.reset_runtime(family))
        row.addWidget(reset)
    else:
        use = QPushButton("Use Remote")
        use.setEnabled(bool(option and option.ready))
        use.clicked.connect(lambda: callbacks.use_start(family, option) if option else None)
        row.addWidget(use)
    health = QPushButton("Health Check")
    health.setEnabled(bool(option and option.ready))
    health.clicked.connect(lambda: callbacks.health(option.worker_id, option.model_id) if option else None)
    row.addWidget(health)
    row.addStretch(1)
    card.layout().addLayout(row)

    progress = str(state.get("progress") or "")
    error = str(state.get("error") or "")
    logs = str(state.get("log_tail") or "")
    if progress or error or logs:
        detail = QLabel("\n".join(value for value in (progress, error, logs) if value))
        detail.setWordWrap(True)
        detail.setObjectName("CommandText")
        card.layout().addWidget(detail)

    import_button = QPushButton("Import Provider Manifest…")
    import_button.clicked.connect(lambda: callbacks.import_manifest(family))
    return Column(
        card,
        service_access_card(access[0], access[1], access_callbacks),
        _button_card("Provider catalog", import_button),
    )


def _mlp_family(
    models: tuple[Mapping, ...], candidate: str, state: Mapping, callbacks: ServiceCallbacks,
    access: tuple[VisionAccessIdentity, ManagedCredentialState],
    access_callbacks: AccessCallbacks,
) -> QWidget:
    card = Card(
        "Custom MLP inference service — Preview",
        "Model loading and composed IK execution are disabled until the multi-provider pipeline is designed.",
        muted=False,
    )
    form = QFormLayout()
    combo = QComboBox()
    combo.addItem("No trained model selected", "")
    for model in models:
        combo.addItem(f"{model['model_id']} — {'active record' if model.get('active') else 'shadow record'}", model["model_id"])
    combo.setCurrentIndex(max(0, combo.findData(candidate)))
    combo.currentIndexChanged.connect(
        lambda: callbacks.candidate_changed(
            "mlp", ProviderOption("mlp", str(combo.currentData() or ""), "ik-inference-1")
        )
    )
    form.addRow("Model", combo)
    form.addRow("Runtime", QLabel("Installed" if state.get("runtime_installed") else "Not installed"))
    form.addRow("Process", QLabel(str(state.get("process_state") or "stopped").title()))
    form.addRow("MQTT", QLabel(str(state.get("mqtt_state") or "offline").title()))
    card.layout().addLayout(form)
    toggle = QCheckBox("Start with Studio")
    toggle.setChecked(bool(state.get("start_with_studio")))
    toggle.toggled.connect(lambda checked: callbacks.autostart_changed("mlp", checked))
    card.layout().addWidget(toggle)
    row = QHBoxLayout()
    setup = QPushButton("Set Up Runtime" if not state.get("runtime_installed") else "Repair Runtime")
    setup.clicked.connect(lambda: callbacks.install("mlp", bool(state.get("runtime_installed"))))
    row.addWidget(setup)
    start = QPushButton("Start")
    start.setEnabled(bool(state.get("runtime_installed")) and state.get("process_state") == "stopped")
    start.clicked.connect(lambda: callbacks.start("mlp"))
    row.addWidget(start)
    stop = QPushButton("Stop")
    stop.setEnabled(state.get("process_state") in {"starting", "running"})
    stop.clicked.connect(lambda: callbacks.stop("mlp"))
    row.addWidget(stop)
    restart = QPushButton("Restart")
    restart.setEnabled(state.get("process_state") in {"starting", "running"})
    restart.clicked.connect(lambda: callbacks.restart("mlp"))
    row.addWidget(restart)
    health = QPushButton("Health Check")
    health.setEnabled(state.get("mqtt_state") == "ready")
    health.clicked.connect(lambda: callbacks.health("ik-inference-1", candidate or "*"))
    row.addWidget(health)
    reset = QPushButton("Reset Runtime")
    reset.setEnabled(bool(state.get("runtime_installed")) and state.get("process_state") == "stopped")
    reset.clicked.connect(lambda: callbacks.reset_runtime("mlp"))
    row.addWidget(reset)
    card.layout().addLayout(row)
    return Column(card, service_access_card(access[0], access[1], access_callbacks))


def _button_card(title: str, button: QPushButton) -> QWidget:
    card = Card(title, "Add a pinned model that uses one of Studio's vetted adapters.", muted=False)
    card.layout().addWidget(button)
    return card
