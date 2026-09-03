"""Validated model manifests used by Studio and managed worker runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
from typing import Any, Mapping

MANIFEST_SCHEMA = "desk_buddy.vision.provider-manifest.v1"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,159}$")
SAFE_WORKER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
IMMUTABLE_REVISION = re.compile(r"^[0-9a-f]{40,64}$")
SOURCE_ID = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

DEPENDENCY_PROFILES = {
    "zero-shot-hf": ("paho-mqtt>=2.1,<3", "Pillow>=10,<12", "torch>=2.3", "transformers>=4.46,<5"),
    "depth-hf": (
        "paho-mqtt>=2.1,<3", "numpy>=1.26,<3", "Pillow>=10,<12",
        "torch>=2.3", "transformers>=4.46,<5",
    ),
}
ADAPTERS = {
    "huggingface-zero-shot.v1": {
        "family": "detection", "kind": "zero_shot.infer", "profile": "zero-shot-hf",
        "entry_point": "studio.services.vision.zero_shot.worker_app",
        "input_schema": "image.jpeg.v1", "output_schema": "detections.v1",
    },
    "huggingface-depth.v1": {
        "family": "depth", "kind": "depth.infer", "profile": "depth-hf",
        "entry_point": "studio.services.vision.depth.worker_app",
        "input_schema": "image.jpeg.v1", "output_schema": "depth.v1",
    },
}
FORBIDDEN_MANIFEST_KEYS = {
    "command", "install_command", "entry_point", "password", "credential", "credentials",
    "token", "api_key", "secret", "shell", "script", "code", "trust_remote_code",
}
ALLOWED_MANIFEST_KEYS = {
    "schema", "family", "model_id", "worker_id", "adapter_id",
    "dependency_profile", "provider", "source", "revision", "license",
    "size_mb", "devices", "schemas", "requirements",
}


@dataclass(frozen=True)
class ProviderManifestV1:
    model_id: str
    kind: str
    provider: str
    source: str
    revision: str
    worker_id: str
    entry_point: str
    requirements: tuple[str, ...]
    input_schema: str
    output_schema: str
    license: str = "unknown"
    size_mb: int | None = None
    devices: tuple[str, ...] = ("cpu",)
    family: str = ""
    adapter_id: str = ""
    dependency_profile: str = ""
    schema: str = MANIFEST_SCHEMA

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProviderManifestV1":
        _reject_forbidden(raw)
        unknown = sorted(set(raw) - ALLOWED_MANIFEST_KEYS)
        if unknown:
            raise ValueError(f"unsupported manifest field(s): {', '.join(unknown)}")
        if raw.get("schema") != MANIFEST_SCHEMA:
            raise ValueError(f"manifest schema must be {MANIFEST_SCHEMA}")
        adapter_id = str(raw.get("adapter_id") or "")
        adapter = ADAPTERS.get(adapter_id)
        if adapter is None:
            raise ValueError(f"unsupported adapter_id: {adapter_id or '(missing)'}")
        required = (
            "family", "model_id", "provider", "source", "revision", "worker_id",
            "license", "size_mb", "devices", "schemas", "dependency_profile",
        )
        missing = [name for name in required if not str(raw.get(name) or "").strip()]
        if missing:
            raise ValueError(f"model manifest is missing: {', '.join(missing)}")
        model_id, worker_id = str(raw["model_id"]), str(raw["worker_id"])
        if not SAFE_ID.fullmatch(model_id) or not SAFE_WORKER_ID.fullmatch(worker_id):
            raise ValueError("model_id is invalid or worker_id is not a 2-32 character MQTT-safe lowercase ID")
        revision = str(raw["revision"]).lower()
        if not IMMUTABLE_REVISION.fullmatch(revision):
            raise ValueError("revision must be a complete immutable hexadecimal commit")
        source = str(raw["source"])
        if not SOURCE_ID.fullmatch(source):
            raise ValueError("source must be a repository ID such as organization/model")
        family = str(raw.get("family") or adapter["family"])
        if family != adapter["family"]:
            raise ValueError(f"adapter {adapter_id} belongs to the {adapter['family']} family")
        profile = str(raw.get("dependency_profile") or adapter["profile"])
        if profile != adapter["profile"]:
            raise ValueError(f"adapter {adapter_id} requires dependency profile {adapter['profile']}")
        requirements = tuple(str(item) for item in raw.get("requirements", DEPENDENCY_PROFILES[profile]))
        if requirements != DEPENDENCY_PROFILES[profile]:
            raise ValueError("custom dependency packages are not allowed in provider manifests")
        devices = tuple(str(item) for item in raw.get("devices", ()))
        if not devices or any(item not in {"cpu", "cuda"} for item in devices):
            raise ValueError("devices must contain cpu and/or cuda")
        schemas = tuple(str(item) for item in raw.get("schemas", ()))
        expected_schemas = (str(adapter["input_schema"]), str(adapter["output_schema"]))
        if schemas != expected_schemas:
            raise ValueError(f"unsupported schemas; {adapter_id} requires {expected_schemas}")
        size_mb = raw.get("size_mb")
        if isinstance(size_mb, bool) or not isinstance(size_mb, int) or size_mb <= 0:
            raise ValueError("size_mb must be a positive integer")
        return cls(
            model_id=model_id,
            kind=str(adapter["kind"]),
            provider=str(raw["provider"]),
            source=source,
            revision=revision,
            worker_id=worker_id,
            entry_point=str(adapter["entry_point"]),
            requirements=requirements,
            input_schema=str(adapter["input_schema"]),
            output_schema=str(adapter["output_schema"]),
            license=str(raw.get("license") or "unknown"),
            size_mb=size_mb,
            devices=devices,
            family=family,
            adapter_id=adapter_id,
            dependency_profile=profile,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "kind": self.kind,
            "provider": self.provider,
            "source": self.source,
            "revision": self.revision,
            "worker_id": self.worker_id,
            "entry_point": self.entry_point,
            "requirements": list(self.requirements),
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "license": self.license,
            "size_mb": self.size_mb,
            "devices": list(self.devices),
            "family": self.family,
            "adapter_id": self.adapter_id,
            "dependency_profile": self.dependency_profile,
            "schema": self.schema,
        }


ModelManifest = ProviderManifestV1


def load_provider_manifest(path: str | Path) -> ProviderManifestV1:
    with Path(path).open("rb") as handle:
        raw = tomllib.load(handle)
    if set(raw) != {"provider"}:
        raise ValueError("manifest may contain only one [provider] table")
    provider = raw.get("provider")
    if not isinstance(provider, dict):
        raise ValueError("manifest requires one [provider] table")
    return ProviderManifestV1.from_dict(provider)


def imported_manifests(directory: str | Path) -> tuple[ProviderManifestV1, ...]:
    root = Path(directory)
    if not root.exists():
        return ()
    manifests, seen = [], set()
    for path in sorted(root.glob("*.toml")):
        manifest = load_provider_manifest(path)
        key = (manifest.family, manifest.model_id, manifest.worker_id)
        if key in seen:
            raise ValueError(f"duplicate imported provider route: {manifest.model_id} on {manifest.worker_id}")
        seen.add(key)
        manifests.append(manifest)
    return tuple(manifests)


def _reject_forbidden(value: Any, prefix: str = "provider") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key)
            if name.lower() in FORBIDDEN_MANIFEST_KEYS:
                raise ValueError(f"{prefix}.{name} is not allowed")
            _reject_forbidden(child, f"{prefix}.{name}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_forbidden(child, f"{prefix}[{index}]")


BUILTIN_MANIFESTS = (
    ProviderManifestV1(
        model_id="owlv2-base",
        kind="zero_shot.infer",
        provider="Hugging Face / Google",
        source="google/owlv2-base-patch16",
        revision="2a1560802f8cf3c408fec9b809d705f56a2f7146",
        worker_id="zero-shot-hf-1",
        entry_point="studio.services.vision.zero_shot.worker_app",
        requirements=("torch", "transformers", "Pillow", "paho-mqtt"),
        input_schema="image.jpeg.v1",
        output_schema="detections.v1",
        license="Apache-2.0 (verify at pinned revision)",
        size_mb=620,
        devices=("cpu", "cuda"),
        family="detection",
        adapter_id="huggingface-zero-shot.v1",
        dependency_profile="zero-shot-hf",
    ),
    ProviderManifestV1(
        model_id="owlv2-base-ensemble",
        kind="zero_shot.infer",
        provider="Hugging Face / Google",
        source="google/owlv2-base-patch16-ensemble",
        revision="cfd3195ba4ea9592eec887ded089f4c08eff231d",
        worker_id="zero-shot-owlv2-ensemble-1",
        entry_point="studio.services.vision.zero_shot.worker_app",
        requirements=("torch", "transformers", "Pillow", "paho-mqtt"),
        input_schema="image.jpeg.v1",
        output_schema="detections.v1",
        license="Apache-2.0 (verify at pinned revision)",
        size_mb=620,
        devices=("cpu", "cuda"),
        family="detection",
        adapter_id="huggingface-zero-shot.v1",
        dependency_profile="zero-shot-hf",
    ),
    ProviderManifestV1(
        model_id="depth-anything-v2-small",
        kind="depth.infer",
        provider="Hugging Face / Depth Anything",
        source="depth-anything/Depth-Anything-V2-Small-hf",
        revision="5426e4f0f36572d16453bbda7a8389317b1bef99",
        worker_id="depth-hf-1",
        entry_point="studio.services.vision.depth.worker_app",
        requirements=("torch", "transformers", "numpy", "Pillow", "paho-mqtt"),
        input_schema="image.jpeg.v1",
        output_schema="depth.v1",
        license="Apache-2.0 (verify at pinned revision)",
        size_mb=100,
        devices=("cpu", "cuda"),
        family="depth",
        adapter_id="huggingface-depth.v1",
        dependency_profile="depth-hf",
    ),
)
