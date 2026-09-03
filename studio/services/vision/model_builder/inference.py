"""MQTT handler that loads pinned IK bundles and serves canonical inference."""

from __future__ import annotations

from io import BytesIO
from typing import Callable, Mapping

from ..contracts import IKPredictionV1, JobEnvelope, VisionObservationV1
from ..frames import BinaryArtifact
from ..worker import WorkerOutput
from .features import feature_rows, provider_signature
from .network import checkpoint_metadata, build_model
from .training import MODEL_BUNDLE_SCHEMA


class IKInferenceHandler:
    model_id = "*"
    model_version = "dynamic"
    job_kinds = ("ik_model.load", "ik_model.infer")
    input_schemas = (MODEL_BUNDLE_SCHEMA, "vision-observation.v1")
    output_schemas = ("ik-prediction.v1",)

    def __init__(self, *, device: str = "cpu") -> None:
        self.device_name = device
        self._models: dict[str, tuple[object, dict[str, object]]] = {}
        self._status_callback: Callable[[], None] | None = None

    def set_status_callback(self, callback: Callable[[], None]) -> None:
        self._status_callback = callback

    def advertised_models(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {"model_id": model_id, "model_version": metadata["model_version"],
             "job_kinds": ["ik_model.infer", "ik_model.load"], "provider": metadata["provider"]}
            for model_id, (_, metadata) in sorted(self._models.items())
        )

    def version_for(self, model_id: str) -> str:
        loaded = self._models.get(model_id)
        return str(loaded[1]["model_version"]) if loaded is not None else ""

    def handle(self, job: JobEnvelope, artifacts: Mapping[str, BinaryArtifact]) -> WorkerOutput:
        if job.kind == "ik_model.load":
            return self._load(job, artifacts)
        return self._infer(job)

    def _load(self, job: JobEnvelope, artifacts: Mapping[str, BinaryArtifact]) -> WorkerOutput:
        import torch

        artifact = artifacts.get("model")
        if artifact is None:
            raise ValueError("model load requires a model artifact")
        try:
            checkpoint = torch.load(BytesIO(artifact.payload), map_location=self.device_name, weights_only=True)
        except TypeError:
            checkpoint = torch.load(BytesIO(artifact.payload), map_location=self.device_name)
        metadata = checkpoint_metadata(dict(checkpoint["metadata"]))
        if metadata["schema"] != MODEL_BUNDLE_SCHEMA:
            raise ValueError("unsupported model bundle schema")
        if metadata["model_id"] != job.model_id:
            raise ValueError("job model ID does not match the bundle")
        model = build_model().to(self.device_name)
        model.load_state_dict(checkpoint["state_dict"], strict=True)
        model.eval()
        # One inference process represents one selected MLP service. Keeping
        # old checkpoints resident wastes device memory and makes the worker's
        # advertised route ambiguous.
        self._replace_loaded_model(job.model_id, model, metadata)
        return WorkerOutput(payload={"model": metadata, "loaded": True})

    def _replace_loaded_model(
        self, model_id: str, model: object, metadata: dict[str, object]
    ) -> None:
        """Atomically replace the one model this inference service represents."""
        self._models = {model_id: (model, metadata)}
        if self._status_callback:
            self._status_callback()

    def _infer(self, job: JobEnvelope) -> WorkerOutput:
        import torch

        loaded = self._models.get(job.model_id)
        if loaded is None:
            raise ValueError(f"model {job.model_id} is not loaded")
        model, metadata = loaded
        observation = VisionObservationV1.from_dict(job.payload.get("observation", {}))
        if provider_signature(observation) != metadata["provider"]:
            raise ValueError("observation provider versions do not match the loaded model")
        rotation_features, control_features = feature_rows(observation)
        with torch.no_grad():
            rotation, control = model(
                torch.tensor([rotation_features], dtype=torch.float32, device=self.device_name),
                torch.tensor([control_features], dtype=torch.float32, device=self.device_name),
            )
        limits = metadata["limits"]
        prediction = IKPredictionV1(
            rotation_deg=float(rotation[0, 0].item()) * float(limits["rotation"]),
            distance_mm=float(control[0, 0].item()) * float(limits["distance"]),
            z_height_mm=float(control[0, 1].item()) * (float(limits["z_max"]) - float(limits["z_min"])) + float(limits["z_min"]),
            model_id=job.model_id,
            model_version=str(metadata["model_version"]),
        )
        return WorkerOutput(payload={"prediction": prediction.as_dict()})
