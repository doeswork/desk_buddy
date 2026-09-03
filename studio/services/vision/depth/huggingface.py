"""Hugging Face monocular depth normalized to DepthMapV1."""

from __future__ import annotations

from io import BytesIO
from typing import Any, Mapping

from ..contracts import DEPTH_SCHEMA, DepthMapV1, JobEnvelope
from ..frames import BinaryArtifact
from ..worker import GeneratedArtifact, WorkerOutput


def depth_array(output: Any):
    import numpy as np
    from PIL import Image

    if not isinstance(output, dict):
        raise RuntimeError("depth pipeline returned a non-object result")
    value = output.get("predicted_depth")
    if value is None:
        value = output.get("depth")
    if value is None:
        raise RuntimeError("depth result has no predicted_depth or depth")
    if hasattr(value, "detach"):
        value = value.detach().float().cpu().numpy()
    elif isinstance(value, Image.Image):
        value = np.asarray(value)
    else:
        value = np.asarray(value)
    while value.ndim > 2:
        value = value[0]
    if value.ndim != 2:
        raise RuntimeError(f"depth output must be 2D, got {value.shape}")
    result = np.ascontiguousarray(value, dtype=np.float32)
    if not np.all(np.isfinite(result)):
        raise RuntimeError("depth output contains non-finite values")
    return result


def compressed_depth(values: Any) -> bytes:
    import numpy as np

    output = BytesIO()
    np.savez_compressed(output, depth=np.ascontiguousarray(values, dtype=np.float32))
    return output.getvalue()


def depth_preview(values: Any) -> bytes:
    import numpy as np
    from PIL import Image

    x = np.clip(values, 0.0, 1.0)
    red = np.clip(1.5 - np.abs(4.0 * x - 3.0), 0.0, 1.0)
    green = np.clip(1.5 - np.abs(4.0 * x - 2.0), 0.0, 1.0)
    blue = np.clip(1.5 - np.abs(4.0 * x - 1.0), 0.0, 1.0)
    image = Image.fromarray(np.asarray(np.stack((red, green, blue), axis=-1) * 255.0, dtype=np.uint8))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class HuggingFaceDepthHandler:
    job_kinds = ("depth.infer",)
    input_schemas = ("image.jpeg.v1",)
    output_schemas = (DEPTH_SCHEMA,)

    def __init__(
        self,
        *,
        model_id: str,
        source: str,
        revision: str,
        local_files_only: bool = True,
        device: str = "auto",
        native_near_is_high: bool = True,
    ) -> None:
        self.model_id = model_id
        self.source = source
        self.model_version = revision
        self.local_files_only = local_files_only
        self.device = device
        self.native_near_is_high = native_near_is_high
        self._pipeline: Any = None

    def load(self) -> None:
        if self._pipeline is not None:
            return
        import torch
        from transformers import pipeline

        if self.device == "auto":
            target: int | str = 0 if torch.cuda.is_available() else -1
        elif self.device == "cpu":
            target = -1
        else:
            target = self.device
        self._pipeline = pipeline(
            "depth-estimation",
            model=self.source,
            revision=self.model_version,
            device=target,
            local_files_only=self.local_files_only,
        )

    def handle(self, job: JobEnvelope, artifacts: Mapping[str, BinaryArtifact]) -> WorkerOutput:
        import numpy as np
        from PIL import Image

        self.load()
        image_artifact = artifacts.get("image")
        if image_artifact is None:
            raise ValueError("depth.infer requires the image artifact role")
        image = Image.open(BytesIO(image_artifact.payload)).convert("RGB")
        raw = depth_array(self._pipeline(image))
        minimum = float(np.min(raw))
        maximum = float(np.max(raw))
        span = maximum - minimum
        normalized = np.zeros_like(raw) if span <= 1e-12 else (raw - minimum) / span
        if not self.native_near_is_high:
            normalized = 1.0 - normalized
        normalized = np.ascontiguousarray(np.clip(normalized, 0.0, 1.0), dtype=np.float32)
        height, width = normalized.shape
        normalized_id = f"{job.job_id}-depth"
        preview_id = f"{job.job_id}-depth-preview"
        result = DepthMapV1(
            width=width,
            height=height,
            normalized_artifact_id=normalized_id,
            preview_artifact_id=preview_id,
            native_units="relative",
            native_near_is_high=self.native_near_is_high,
            model_id=self.model_id,
            model_version=self.model_version,
        )
        metadata = {
            "schema": DEPTH_SCHEMA,
            "width": width,
            "height": height,
            "dtype": "float32",
            "canonical_near_is_high": True,
            "native_min": minimum,
            "native_max": maximum,
            "model_id": self.model_id,
            "model_version": self.model_version,
        }
        return WorkerOutput(
            payload={"depth_map": result.as_dict()},
            artifacts=(
                GeneratedArtifact(
                    role="normalized_depth",
                    mime_type="application/x-npz",
                    payload=compressed_depth(normalized),
                    artifact_id=normalized_id,
                    metadata=metadata,
                ),
                GeneratedArtifact(
                    role="depth_preview",
                    mime_type="image/png",
                    payload=depth_preview(normalized),
                    artifact_id=preview_id,
                    metadata=metadata,
                ),
            ),
        )

