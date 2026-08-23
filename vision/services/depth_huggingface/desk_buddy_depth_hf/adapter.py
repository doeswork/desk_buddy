from __future__ import annotations

from io import BytesIO
from typing import Any, Mapping

import numpy as np
from PIL import Image

from desk_buddy_vision_protocol import (
    ArtifactInput,
    GeneratedArtifact,
    JobEnvelope,
    WorkerOutput,
)


def _array_from_output(output: Any) -> np.ndarray:
    if not isinstance(output, dict):
        raise RuntimeError("depth pipeline returned a non-object result")
    value = output.get("predicted_depth")
    if value is None:
        value = output.get("depth")
    if value is None:
        raise RuntimeError("depth pipeline result has no predicted_depth or depth")
    if hasattr(value, "detach"):
        value = value.detach().float().cpu().numpy()
    elif isinstance(value, Image.Image):
        value = np.asarray(value)
    else:
        value = np.asarray(value)
    while value.ndim > 2:
        value = value[0]
    if value.ndim != 2:
        raise RuntimeError(f"depth output must be 2D, got shape {value.shape}")
    result = np.ascontiguousarray(value, dtype=np.float32)
    if not np.all(np.isfinite(result)):
        raise RuntimeError("depth output contains non-finite values")
    return result


def _npy_bytes(values: np.ndarray) -> bytes:
    buffer = BytesIO()
    np.save(buffer, np.ascontiguousarray(values), allow_pickle=False)
    return buffer.getvalue()


def _color_preview(values: np.ndarray) -> bytes:
    x = np.clip(values, 0.0, 1.0)
    red = np.clip(1.5 - np.abs(4.0 * x - 3.0), 0.0, 1.0)
    green = np.clip(1.5 - np.abs(4.0 * x - 2.0), 0.0, 1.0)
    blue = np.clip(1.5 - np.abs(4.0 * x - 1.0), 0.0, 1.0)
    rgb = np.stack((red, green, blue), axis=-1)
    image = Image.fromarray(np.asarray(rgb * 255.0, dtype=np.uint8), mode="RGB")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class HuggingFaceDepthHandler:
    job_kinds = ("depth.infer",)
    input_schemas = ("image.jpeg.v1",)
    output_schemas = ("depth.v1",)

    def __init__(
        self,
        *,
        model_id: str,
        source: str,
        model_version: str,
        local_files_only: bool = True,
        compute_device: str = "auto",
        invert: bool = False,
    ) -> None:
        self.model_id = model_id
        self.source = source
        self.model_version = model_version
        self.local_files_only = local_files_only
        self.compute_device = compute_device
        self.invert = invert
        self._pipeline: Any = None

    def load(self) -> None:
        if self._pipeline is not None:
            return
        import torch
        from transformers import pipeline

        if self.compute_device == "auto":
            device: int | str = 0 if torch.cuda.is_available() else -1
        elif self.compute_device == "cpu":
            device = -1
        else:
            device = self.compute_device
        self._pipeline = pipeline(
            task="depth-estimation",
            model=self.source,
            device=device,
            local_files_only=self.local_files_only,
        )

    def handle(self, job: JobEnvelope, artifacts: Mapping[str, ArtifactInput]) -> WorkerOutput:
        self.load()
        if len(artifacts) != 1:
            raise ValueError("depth.infer requires exactly one image artifact")
        image = Image.open(BytesIO(next(iter(artifacts.values())).payload)).convert("RGB")
        raw = _array_from_output(self._pipeline(image))
        minimum = float(np.min(raw))
        maximum = float(np.max(raw))
        span = maximum - minimum
        normalized = np.zeros_like(raw) if span <= 1e-12 else (raw - minimum) / span
        if self.invert:
            normalized = 1.0 - normalized
        normalized = np.ascontiguousarray(normalized, dtype=np.float32)
        height, width = raw.shape
        artifacts_out = (
            GeneratedArtifact(
                role="raw_depth",
                kind="depth_raw",
                mime_type="application/x-npy",
                payload=_npy_bytes(raw),
                width=width,
                height=height,
                dtype="float32",
                metadata={"units": "relative"},
            ),
            GeneratedArtifact(
                role="normalized_depth",
                kind="depth_normalized",
                mime_type="application/x-npy",
                payload=_npy_bytes(normalized),
                width=width,
                height=height,
                dtype="float32",
                metadata={"range": [0.0, 1.0], "inverted": self.invert},
            ),
            GeneratedArtifact(
                role="depth_preview",
                kind="depth_preview",
                mime_type="image/png",
                payload=_color_preview(normalized),
                width=width,
                height=height,
                dtype="uint8",
            ),
        )
        return WorkerOutput(
            payload={
                "depth": {
                    "output_schema": "depth.v1",
                    "width": width,
                    "height": height,
                    "dtype": "float32",
                    "min_value": minimum,
                    "max_value": maximum,
                    "units": "relative",
                    "near_is_high": not self.invert,
                    "source_model_id": self.model_id,
                    "source_model_version": self.model_version,
                }
            },
            artifacts=artifacts_out,
        )

