"""Split-head PyTorch network and checkpoint metadata helpers."""

from __future__ import annotations

from typing import Any

from .features import CONTROL_INPUT_SIZE, ROTATION_INPUT_SIZE

MODEL_ARCHITECTURE = "split-head-mlp.v1"


def build_model():
    import torch.nn as nn

    class SplitHeadMLP(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.rotation_head = nn.Sequential(
                nn.Linear(ROTATION_INPUT_SIZE, 48), nn.ReLU(), nn.Linear(48, 24), nn.ReLU(), nn.Linear(24, 1)
            )
            self.control_head = nn.Sequential(
                nn.Linear(CONTROL_INPUT_SIZE, 96), nn.ReLU(), nn.Linear(96, 48), nn.ReLU(), nn.Linear(48, 2)
            )

        def forward(self, rotation_features, control_features):
            return self.rotation_head(rotation_features), self.control_head(control_features)

    return SplitHeadMLP()


def checkpoint_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    required = {"schema", "model_id", "model_version", "architecture", "feature_schema", "provider", "metrics", "limits"}
    missing = required - raw.keys()
    if missing:
        raise ValueError(f"model metadata missing: {', '.join(sorted(missing))}")
    if raw["architecture"] != MODEL_ARCHITECTURE:
        raise ValueError("unsupported model architecture")
    return dict(raw)
