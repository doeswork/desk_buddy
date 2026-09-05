"""Compact managed Vision services dashboard and its disclosed details."""

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
    QWidget,
)

from ...components import Card, Column
from ....services.vision.access import ManagedCredentialState, VisionAccessIdentity
from .access_widgets import AccessCallbacks, service_access_card
from .disclosure import DisclosureAccordion, DisclosureSpec
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


@dataclass(frozen=True)
class ServicePresentation:
    model: str
    setup: str
    process: str
    mqtt: str
    primary_action: str
    primary_label: str
    primary_enabled: bool = True


def services_dashboard(
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
    external_worker_count: int,
    callbacks: ServiceCallbacks,
    access_callbacks: AccessCallbacks,
    controller_access: tuple[VisionAccessIdentity, ManagedCredentialState],
    detector_access: tuple[VisionAccessIdentity, ManagedCredentialState],
    depth_access: tuple[VisionAccessIdentity, ManagedCredentialState],
    mlp_access: tuple[VisionAccessIdentity, ManagedCredentialState],
    access_states: tuple[ManagedCredentialState, ...],
    controller_connected: bool,
    broker_running: bool,
    setup_access_enabled: bool,
    expanded_service: str = "",
    expansion_changed: Callable[[str], None] | None = None,
) -> QWidget:
    detector = _selected(detector_options, detector_candidate)
    depth = _selected(depth_options, depth_candidate)
    specs = (
        _mqtt_spec(
            controller_access, access_states, access_callbacks,
            connected=controller_connected, broker_running=broker_running,
            setup_enabled=setup_access_enabled,
        ),
        _provider_spec(
            "detection", "Detection", detector_options, detector,
            managed_states.get("detection", {}), manifests, callbacks,
            detector_access, access_callbacks,
        ),
        _provider_spec(
            "depth", "Depth", depth_options, depth,
            managed_states.get("depth", {}), manifests, callbacks,
            depth_access, access_callbacks,
        ),
        _mlp_spec(
            tuple(learned_models), mlp_candidate, managed_states.get("mlp", {}),
            callbacks, mlp_access, access_callbacks,
        ),
        DisclosureSpec(
            key="external",
            title="Advanced workers",
            model=f"{external_worker_count} configured",
            setup="Compatibility path",
            process="Manual lifecycle",
            mqtt="MQTT only",
            detail=external_workers,
        ),
    )
    return DisclosureAccordion(
        specs,
        expanded_key=expanded_service,
        expanded_changed=expansion_changed,
    )


def provider_presentation(
    option: ProviderOption | None,
    state: Mapping,
    *,
    local: bool,
) -> ServicePresentation:
    active_model = str(state.get("active_model_id") or "")
    active_worker = str(state.get("active_worker_id") or "")
    candidate_model = option.model_id if option else ""
    candidate_worker = option.worker_id if option else ""
    same_route = bool(
        option and active_model == candidate_model and active_worker == candidate_worker
    )
    model = active_model or "No model selected"
    if option and not same_route:
        model = f"{model} · candidate {candidate_model}"

    installed = bool(state.get("installed"))
    active_installed = bool(state.get("active_installed"))
    if not local:
        setup = "Remote & selected" if same_route else "Remote candidate"
    elif same_route:
        setup = "Downloaded & selected" if installed else "Selected, not downloaded"
    elif active_model:
        active_text = "downloaded" if active_installed else "not downloaded"
        candidate_text = "downloaded" if installed else "not downloaded"
        setup = f"Selected {active_text}; candidate {candidate_text}"
    else:
        setup = "Candidate downloaded" if installed else "Not downloaded"

    process_state = str(state.get("process_state") or "stopped")
    process = "Remote" if state.get("remote") else _title_state(process_state)
    mqtt = _mqtt_label(str(state.get("mqtt_state") or "offline"))
    operation = str(state.get("operation") or "")
    running = process_state in {"starting", "running"}

    if operation:
        action, label, enabled = "cancel", "Cancel Install", True
    elif not local:
        if same_route:
            action, label, enabled = "health", "Health Check", bool(option and option.ready)
        else:
            action, label, enabled = "use", "Use Remote", bool(option and option.ready)
    elif running:
        action, label, enabled = "stop", "Stop", True
    elif process_state == "stopping":
        action, label, enabled = "none", "Stopping…", False
    elif not installed:
        action, label, enabled = "download", "Download", option is not None
    elif not same_route:
        action, label, enabled = "use", "Use & Start", option is not None
    else:
        action, label, enabled = "start", "Start", True
    return ServicePresentation(model, setup, process, mqtt, action, label, enabled)


def mlp_presentation(candidate: str, state: Mapping) -> ServicePresentation:
    process_state = str(state.get("process_state") or "stopped")
    installed = bool(state.get("runtime_installed"))
    operation = str(state.get("operation") or "")
    if operation:
        action, label, enabled = "cancel", "Cancel Install", True
    elif process_state in {"starting", "running"}:
        action, label, enabled = "stop", "Stop", True
    elif process_state == "stopping":
        action, label, enabled = "none", "Stopping…", False
    elif not installed:
        action, label, enabled = "download", "Set Up Runtime", True
    else:
        action, label, enabled = "start", "Start", True
    return ServicePresentation(
        candidate or "No trained model selected",
        "Runtime installed" if installed else "Runtime not installed",
        _title_state(process_state),
        _mqtt_label(str(state.get("mqtt_state") or "offline")),
        action,
        label,
        enabled,
    )


def _provider_spec(
    family: str,
    title: str,
    options: tuple[ProviderOption, ...],
    option: ProviderOption | None,
    state: Mapping,
    manifests: Mapping[tuple[str, str], object],
    callbacks: ServiceCallbacks,
    access: tuple[VisionAccessIdentity, ManagedCredentialState],
    access_callbacks: AccessCallbacks,
) -> DisclosureSpec:
    local = bool(option and option.key in manifests)
    presentation = provider_presentation(option, state, local=local)
    primary = _provider_primary(family, option, presentation, callbacks)
    detail = _provider_details(
        family, title, options, option, state, manifests, callbacks,
        access, access_callbacks,
    )
    return DisclosureSpec(
        family, title, presentation.model, presentation.setup,
        presentation.process, presentation.mqtt, detail, primary,
    )


def _provider_primary(
    family: str,
    option: ProviderOption | None,
    presentation: ServicePresentation,
    callbacks: ServiceCallbacks,
) -> QPushButton:
    button = QPushButton(_button_text(presentation.primary_label))
    button.setEnabled(presentation.primary_enabled)
    actions = {
        "cancel": callbacks.cancel_install,
        "download": lambda: callbacks.install(family, False),
        "use": lambda: callbacks.use_start(family, option) if option else None,
        "start": lambda: callbacks.start(family),
        "stop": lambda: callbacks.stop(family),
        "health": lambda: callbacks.health(option.worker_id, option.model_id) if option else None,
    }
    callback = actions.get(presentation.primary_action)
    if callback is not None:
        button.clicked.connect(callback)
    return button


def _provider_details(
    family: str,
    title: str,
    options: tuple[ProviderOption, ...],
    option: ProviderOption | None,
    state: Mapping,
    manifests: Mapping[tuple[str, str], object],
    callbacks: ServiceCallbacks,
    access: tuple[VisionAccessIdentity, ManagedCredentialState],
    access_callbacks: AccessCallbacks,
) -> QWidget:
    card = Card(f"{title} configuration", muted=False)
    form = QFormLayout()
    combo = QComboBox()
    selected_index = 0
    for index, candidate in enumerate(options):
        suffix = "Managed" if candidate.key in manifests else "Remote"
        combo.addItem(f"{candidate.model_id} — {candidate.worker_id} ({suffix})", candidate)
        if option and candidate.key == option.key:
            selected_index = index
    combo.setCurrentIndex(selected_index)
    combo.currentIndexChanged.connect(
        lambda: callbacks.candidate_changed(family, combo.currentData())
        if isinstance(combo.currentData(), ProviderOption) else None
    )
    form.addRow("Candidate model", combo)
    active_model = str(state.get("active_model_id") or "None")
    active_worker = str(state.get("active_worker_id") or "")
    form.addRow("Active route", QLabel(
        f"{active_model} — {active_worker}" if active_worker else active_model
    ))

    manifest = manifests.get(option.key) if option else None
    local = manifest is not None
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
    card.layout().addLayout(_provider_controls(family, option, state, local, callbacks))

    progress = str(state.get("progress") or "")
    error = str(state.get("error") or "")
    logs = str(state.get("log_tail") or "")
    if progress or error or logs:
        activity = QLabel("\n".join(value for value in (progress, error, logs) if value))
        activity.setWordWrap(True)
        activity.setObjectName("CommandText")
        card.layout().addWidget(activity)

    catalog = Card(
        "Provider catalog",
        "Add a pinned model that uses one of Studio's vetted adapters.",
        muted=False,
    )
    import_button = QPushButton("Import Provider Manifest…")
    import_button.clicked.connect(lambda: callbacks.import_manifest(family))
    catalog.layout().addWidget(import_button)
    return Column(
        card,
        service_access_card(access[0], access[1], access_callbacks),
        catalog,
    )


def _provider_controls(
    family: str,
    option: ProviderOption | None,
    state: Mapping,
    local: bool,
    callbacks: ServiceCallbacks,
) -> QHBoxLayout:
    row = QHBoxLayout()
    installed = bool(state.get("installed"))
    process_state = str(state.get("process_state") or "stopped")
    running = process_state in {"starting", "running"}
    operation = str(state.get("operation") or "")
    if local and operation:
        _add_button(row, "Cancel Install", True, callbacks.cancel_install)
    elif local:
        _add_button(row, "Download" if not installed else "Repair", not running,
                    lambda: callbacks.install(family, installed))
        _add_button(row, "Use & Start", installed and not running,
                    lambda: callbacks.use_start(family, option) if option else None)
        _add_button(row, "Stop", running, lambda: callbacks.stop(family))
        _add_button(row, "Restart", running, lambda: callbacks.restart(family))
        _add_button(row, "Remove Download", installed and not running,
                    lambda: callbacks.remove_download(family))
        _add_button(row, "Reset Runtime", bool(state.get("runtime_installed")) and not running,
                    lambda: callbacks.reset_runtime(family))
    else:
        _add_button(row, "Use Remote", bool(option and option.ready),
                    lambda: callbacks.use_start(family, option) if option else None)
    _add_button(row, "Health Check", bool(option and option.ready),
                lambda: callbacks.health(option.worker_id, option.model_id) if option else None)
    row.addStretch(1)
    return row


def _mlp_spec(
    models: tuple[Mapping, ...],
    candidate: str,
    state: Mapping,
    callbacks: ServiceCallbacks,
    access: tuple[VisionAccessIdentity, ManagedCredentialState],
    access_callbacks: AccessCallbacks,
) -> DisclosureSpec:
    presentation = mlp_presentation(candidate, state)
    primary = QPushButton(_button_text(presentation.primary_label))
    primary.setEnabled(presentation.primary_enabled)
    actions = {
        "cancel": callbacks.cancel_install,
        "download": lambda: callbacks.install("mlp", False),
        "start": lambda: callbacks.start("mlp"),
        "stop": lambda: callbacks.stop("mlp"),
    }
    callback = actions.get(presentation.primary_action)
    if callback is not None:
        primary.clicked.connect(callback)
    detail = _mlp_details(models, candidate, state, callbacks, access, access_callbacks)
    return DisclosureSpec(
        "mlp", "Custom MLP", presentation.model, presentation.setup,
        presentation.process, presentation.mqtt, detail, primary,
    )


def _mlp_details(
    models: tuple[Mapping, ...], candidate: str, state: Mapping,
    callbacks: ServiceCallbacks,
    access: tuple[VisionAccessIdentity, ManagedCredentialState],
    access_callbacks: AccessCallbacks,
) -> QWidget:
    card = Card(
        "Custom MLP inference configuration — Preview",
        "Model loading and composed IK execution remain disabled until the multi-provider pipeline is designed.",
        muted=False,
    )
    form = QFormLayout()
    combo = QComboBox()
    combo.addItem("No trained model selected", "")
    for model in models:
        state_label = "active record" if model.get("active") else "shadow record"
        combo.addItem(f"{model['model_id']} — {state_label}", model["model_id"])
    combo.setCurrentIndex(max(0, combo.findData(candidate)))
    combo.currentIndexChanged.connect(
        lambda: callbacks.candidate_changed(
            "mlp", ProviderOption("mlp", str(combo.currentData() or ""), "ik-inference-1")
        )
    )
    form.addRow("Model", combo)
    form.addRow("Runtime", QLabel("Installed" if state.get("runtime_installed") else "Not installed"))
    form.addRow("Process", QLabel(_title_state(str(state.get("process_state") or "stopped"))))
    form.addRow("MQTT", QLabel(_mqtt_label(str(state.get("mqtt_state") or "offline"))))
    card.layout().addLayout(form)
    toggle = QCheckBox("Start with Studio")
    toggle.setChecked(bool(state.get("start_with_studio")))
    toggle.toggled.connect(lambda checked: callbacks.autostart_changed("mlp", checked))
    card.layout().addWidget(toggle)

    row = QHBoxLayout()
    installed = bool(state.get("runtime_installed"))
    running = state.get("process_state") in {"starting", "running"}
    _add_button(row, "Set Up Runtime" if not installed else "Repair Runtime", not running,
                lambda: callbacks.install("mlp", installed))
    _add_button(row, "Start", installed and not running, lambda: callbacks.start("mlp"))
    _add_button(row, "Stop", running, lambda: callbacks.stop("mlp"))
    _add_button(row, "Restart", running, lambda: callbacks.restart("mlp"))
    _add_button(row, "Health Check", state.get("mqtt_state") == "ready",
                lambda: callbacks.health("ik-inference-1", candidate or "*"))
    _add_button(row, "Reset Runtime", installed and not running,
                lambda: callbacks.reset_runtime("mlp"))
    row.addStretch(1)
    card.layout().addLayout(row)
    return Column(card, service_access_card(access[0], access[1], access_callbacks))


def _mqtt_spec(
    controller_access: tuple[VisionAccessIdentity, ManagedCredentialState],
    states: tuple[ManagedCredentialState, ...],
    callbacks: AccessCallbacks,
    *,
    connected: bool,
    broker_running: bool,
    setup_enabled: bool,
) -> DisclosureSpec:
    identity, state = controller_access
    ready = sum(item.ready for item in states)
    primary = QPushButton("Set Up/Repair All" if broker_running else "Start && Set Up")
    primary.setEnabled(setup_enabled)
    primary.clicked.connect(callbacks.setup_all)
    config = Card(
        "Studio MQTT configuration",
        "The Studio controller owns robot commands; model workers remain restricted to their directed Vision topics.",
        muted=False,
    )
    form = QFormLayout()
    form.addRow("Endpoint", QLabel(f"{state.host}:{state.port}"))
    form.addRow("Controller", QLabel("Connected" if connected else "Disconnected"))
    form.addRow("Managed identities", QLabel(f"{ready} of {len(states)} ready"))
    config.layout().addLayout(form)
    detail = Column(config, service_access_card(identity, state, callbacks))
    return DisclosureSpec(
        "mqtt", "Studio MQTT", f"{state.host}:{state.port}",
        f"{ready}/{len(states)} access ready",
        "Broker running" if broker_running else "Broker stopped",
        "Connected" if connected else "Disconnected",
        detail,
        primary,
    )


def _selected(
    options: tuple[ProviderOption, ...], key: tuple[str, str]
) -> ProviderOption | None:
    return next((item for item in options if item.key == key), None)


def _mqtt_label(value: str) -> str:
    return {
        "ready": "Connected",
        "waiting": "Waiting",
        "offline": "Offline",
    }.get(value, _title_state(value))


def _title_state(value: str) -> str:
    return value.replace("_", " ").title()


def _add_button(
    row: QHBoxLayout, text: str, enabled: bool, callback: Callable[[], None]
) -> QPushButton:
    button = QPushButton(_button_text(text))
    button.setEnabled(enabled)
    button.clicked.connect(callback)
    row.addWidget(button)
    return button


def _button_text(value: str) -> str:
    """Qt uses ampersands for mnemonics; double them to show a literal one."""
    return value.replace("&", "&&")
