"""The deliberately small, reviewed detector catalog."""

from __future__ import annotations

import re
from dataclasses import dataclass

IMMUTABLE_REVISION = re.compile(r"^[0-9a-f]{40}$")
SOURCE_ID = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9-]+$")


@dataclass(frozen=True)
class ModelManifest:
    """Everything Studio and the generic Hugging Face adapter need."""

    model_id: str
    name: str
    provider: str
    source: str
    revision: str
    size_mb: int
    license: str
    prompt_style: str
    box_threshold: float
    text_threshold: float | None = None

    def __post_init__(self) -> None:
        if not MODEL_ID.fullmatch(self.model_id) or not SOURCE_ID.fullmatch(self.source):
            raise ValueError("a curated model needs a stable ID and Hugging Face source")
        if not IMMUTABLE_REVISION.fullmatch(self.revision):
            raise ValueError("model revisions must be complete immutable commits")
        if self.prompt_style not in {"nested", "sentence"}:
            raise ValueError("unsupported zero-shot prompt style")
        if not self.name or not self.provider or not self.license or self.size_mb <= 0:
            raise ValueError("curated model display metadata is incomplete")
        for value in (self.box_threshold, self.text_threshold):
            if value is not None and not 0 <= value <= 1:
                raise ValueError("model thresholds must be between zero and one")

    def as_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "name": self.name,
            "provider": self.provider,
            "source": self.source,
            "revision": self.revision,
            "size_mb": self.size_mb,
            "license": self.license,
            "prompt_style": self.prompt_style,
            "box_threshold": self.box_threshold,
            "text_threshold": self.text_threshold,
        }


BUILTIN_MODELS = (
    ModelManifest(
        model_id="owlv2-base",
        name="OWLv2 Base",
        provider="Google · Hugging Face",
        source="google/owlv2-base-patch16",
        revision="2a1560802f8cf3c408fec9b809d705f56a2f7146",
        size_mb=620,
        license="Apache-2.0",
        prompt_style="nested",
        box_threshold=0.25,
    ),
    ModelManifest(
        model_id="grounding-dino-tiny",
        name="Grounding DINO Tiny",
        provider="IDEA Research · Hugging Face",
        source="IDEA-Research/grounding-dino-tiny",
        revision="a2bb814dd30d776dcf7e30523b00659f4f141c71",
        size_mb=690,
        license="Apache-2.0",
        prompt_style="sentence",
        box_threshold=0.4,
        text_threshold=0.3,
    ),
)

DEFAULT_MODEL_ID = BUILTIN_MODELS[0].model_id


def validate_catalog(models=BUILTIN_MODELS, default_model_id=DEFAULT_MODEL_ID) -> None:
    ids = [item.model_id for item in models]
    sources = [item.source for item in models]
    if not models or len(ids) != len(set(ids)) or len(sources) != len(set(sources)):
        raise ValueError("curated model IDs and sources must be unique")
    if default_model_id not in ids:
        raise ValueError("the default detector must be in the curated catalog")


validate_catalog()


def model_manifest(model_id: str) -> ModelManifest | None:
    return next((item for item in BUILTIN_MODELS if item.model_id == model_id), None)
