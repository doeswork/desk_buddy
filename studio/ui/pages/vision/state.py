"""View state and provider-option resolution for the Vision page."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping

from ....services.vision.manifests import BUILTIN_MANIFESTS, ModelManifest

ROLE_KINDS = {"detector": "zero_shot.infer", "depth": "depth.infer"}


@dataclass(frozen=True)
class ProviderOption:
    role: str
    model_id: str
    worker_id: str
    source: str = ""
    revision: str = ""
    model_version: str = ""
    ready: bool = False
    busy: bool = False
    status_text: str = "Offline"

    @property
    def key(self) -> tuple[str, str]:
        return self.model_id, self.worker_id

    @property
    def label(self) -> str:
        suffix = "Busy" if self.ready and self.busy else "Ready" if self.ready else "Offline"
        return f"{self.model_id} — {self.worker_id} ({suffix})"


@dataclass
class VisionPageState:
    prompt: str = "cup"
    planner: str = "deterministic"
    learned_model_id: str = ""
    workers: list[dict[str, Any]] = field(default_factory=list)
    latest: dict[str, Any] = field(default_factory=dict)
    service_candidates: dict[str, tuple[str, str]] = field(default_factory=dict)
    managed_services: dict[str, dict[str, Any]] = field(default_factory=dict)
    health: dict[str, Any] = field(default_factory=dict)
    top_tab: int = 0
    expanded_service: str = ""


def provider_options(
    role: str,
    workers: Iterable[Mapping[str, Any]],
    *,
    selected: tuple[str, str] | None = None,
    manifests: Iterable[ModelManifest] = BUILTIN_MANIFESTS,
) -> tuple[ProviderOption, ...]:
    job_kind = ROLE_KINDS[role]
    options: dict[tuple[str, str], ProviderOption] = {}
    order: list[tuple[str, str]] = []
    for manifest in manifests:
        if manifest.kind != job_kind:
            continue
        option = ProviderOption(
            role=role,
            model_id=manifest.model_id,
            worker_id=manifest.worker_id,
            source=manifest.source,
            revision=manifest.revision,
        )
        options[option.key] = option
        order.append(option.key)

    for worker in workers:
        worker_id = str(worker.get("worker_id") or "")
        worker_kinds = tuple(str(value) for value in worker.get("job_kinds", ()))
        if not worker_id or job_kind not in worker_kinds:
            continue
        worker_ready = bool(worker.get("ready"))
        worker_busy = bool(worker.get("busy"))
        for model in worker.get("models", ()):
            model_id = str(model.get("model_id") or "")
            model_kinds = tuple(str(value) for value in model.get("job_kinds", worker_kinds))
            if not model_id or job_kind not in model_kinds:
                continue
            key = (model_id, worker_id)
            current = options.get(key) or ProviderOption(role, model_id, worker_id)
            options[key] = replace(
                current,
                source=current.source or str(model.get("source") or ""),
                revision=current.revision or str(model.get("revision") or ""),
                model_version=str(model.get("model_version") or ""),
                ready=worker_ready,
                busy=worker_busy,
                status_text="Busy" if worker_ready and worker_busy else "Ready" if worker_ready else "Offline",
            )
            if key not in order:
                order.append(key)

    if selected and selected not in options:
        options[selected] = ProviderOption(role, selected[0], selected[1])
        order.insert(0, selected)
    return tuple(options[key] for key in order)


def selected_option(
    options: Iterable[ProviderOption], selected: tuple[str, str]
) -> ProviderOption | None:
    return next((option for option in options if option.key == selected), None)


def learned_model_compatible(
    record: Mapping[str, Any],
    detector: ProviderOption | None,
    depth: ProviderOption | None,
) -> bool:
    if detector is None or depth is None or not detector.ready or not depth.ready:
        return False
    provider = dict(record.get("metadata", {}).get("provider") or {})
    return all(
        (
            provider.get("detector_model_id") == detector.model_id,
            provider.get("detector_model_version") == detector.model_version,
            provider.get("depth_model_id") == depth.model_id,
            provider.get("depth_model_version") == depth.model_version,
        )
    )
