"""Worker-side allowlist; it deliberately has no Studio or Qt imports."""

from __future__ import annotations

import re

IMMUTABLE_REVISION = re.compile(r"^[0-9a-f]{40}$")

CURATED_MODELS = {
    "owlv2-base": {
        "source": "google/owlv2-base-patch16",
        "revision": "2a1560802f8cf3c408fec9b809d705f56a2f7146",
        "prompt_style": "nested",
        "box_threshold": 0.25,
        "text_threshold": None,
    },
    "grounding-dino-tiny": {
        "source": "IDEA-Research/grounding-dino-tiny",
        "revision": "a2bb814dd30d776dcf7e30523b00659f4f141c71",
        "prompt_style": "sentence",
        "box_threshold": 0.4,
        "text_threshold": 0.3,
    },
}


def validate_model(model: dict) -> dict:
    """Reject a changed repository, revision, or adapter behavior."""
    if not isinstance(model, dict):
        raise ValueError("worker model configuration must be an object")
    model_id = model.get("model_id")
    expected = CURATED_MODELS.get(model_id)
    if expected is None:
        raise ValueError("model is not in the curated detector catalog")
    if not IMMUTABLE_REVISION.fullmatch(str(model.get("revision") or "")):
        raise ValueError("model revision is not an immutable commit")
    for name, value in expected.items():
        if model.get(name) != value:
            raise ValueError(f"curated model field does not match: {name}")
    if model.get("trust_remote_code") not in (None, False):
        raise ValueError("custom model code is not allowed")
    cache_dir = model.get("cache_dir")
    if not isinstance(cache_dir, str) or not cache_dir:
        raise ValueError("model cache directory is required")
    return model
