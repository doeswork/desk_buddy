from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping

from desk_buddy_vision_protocol import ArtifactInput, GeneratedArtifact, JobEnvelope, WorkerOutput

from .model import ResidualLinearModel, train_ridge


class ResidualPlannerHandler:
    job_kinds = ("residual.predict", "training.start", "model.activate")
    input_schemas = ("features.v1", "training-manifest.v1", "planner-model.v1")
    output_schemas = ("plan-corrections.v1", "training-result.v1")

    def __init__(self, *, model_id: str, configured_version: str = "unloaded") -> None:
        self.model_id = model_id
        self._configured_version = configured_version
        self._active: ResidualLinearModel | None = None

    @property
    def model_version(self) -> str:
        return self._active.version if self._active else self._configured_version

    def handle(self, job: JobEnvelope, artifacts: Mapping[str, ArtifactInput]) -> WorkerOutput:
        if job.kind == "residual.predict":
            return self._predict(job)
        if job.kind == "training.start":
            return self._train(job, artifacts)
        if job.kind == "model.activate":
            return self._activate(job, artifacts)
        raise ValueError(f"unsupported planner job: {job.kind}")

    def _predict(self, job: JobEnvelope) -> WorkerOutput:
        if self._active is None:
            raise RuntimeError("planner model is not active in this worker")
        features = job.payload.get("features")
        if not isinstance(features, dict):
            raise ValueError("residual.predict requires features")
        return WorkerOutput(
            payload={
                "output_schema": "plan-corrections.v1",
                "corrections": self._active.predict(features),
                "feature_schema_version": "features.v1",
                "planner_model_id": self._active.model_id,
                "planner_model_version": self._active.version,
            }
        )

    def _train(self, job: JobEnvelope, artifacts: Mapping[str, ArtifactInput]) -> WorkerOutput:
        manifest_id = str(job.payload.get("manifest_artifact_id") or "")
        manifest_input = artifacts.get(manifest_id)
        if manifest_input is None:
            raise ValueError("training manifest artifact is missing")
        manifest = json.loads(manifest_input.payload.decode("utf-8"))
        if manifest.get("schema") != "training-manifest.v1" or manifest.get("feature_schema_version") != "features.v1":
            raise ValueError("training manifest schema is incompatible")
        examples: list[dict] = []
        for row in manifest.get("examples", []):
            artifact_id = str(row.get("feature_artifact_id") or "")
            source = artifacts.get(artifact_id)
            if source is None:
                raise ValueError(f"training feature artifact is missing: {artifact_id}")
            value = json.loads(source.payload.decode("utf-8"))
            examples.append({"features": value["features"], "targets": value["targets"]})
        requested_name = str(job.payload.get("planner_model_name") or self.model_id)
        created = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        version = f"{requested_name}-{created}"
        model, metrics = train_ridge(
            examples,
            model_id=self.model_id,
            version=version,
            seed=int(job.payload.get("seed", manifest.get("seed", 42))),
            validation_split=float(job.payload.get("validation_split", manifest.get("validation_split", 0.2))),
            regularization=float(job.payload.get("regularization", 1.0)),
        )
        metadata = model.metadata() | {
            "training_manifest_artifact_id": manifest_id,
            "safety_bounds": dict(job.payload.get("safety_bounds") or {}),
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        return WorkerOutput(
            payload={
                "output_schema": "training-result.v1",
                "trained_model_id": self.model_id,
                "trained_model_version": version,
                "metrics": metrics,
                "activation_required": True,
            },
            artifacts=(
                GeneratedArtifact(
                    role="model_weights",
                    kind="planner_model_weights",
                    mime_type="application/octet-stream",
                    payload=model.weights_bytes(),
                    source_model_id=self.model_id,
                    source_model_version=version,
                    metadata={"bundle_role": "weights"},
                ),
                GeneratedArtifact(
                    role="model_metadata",
                    kind="planner_model_metadata",
                    mime_type="application/json",
                    payload=json.dumps(metadata, separators=(",", ":"), sort_keys=True).encode("utf-8"),
                    source_model_id=self.model_id,
                    source_model_version=version,
                    metadata={"bundle_role": "metadata"},
                ),
                GeneratedArtifact(
                    role="training_metrics",
                    kind="training_metrics",
                    mime_type="application/json",
                    payload=json.dumps(metrics, separators=(",", ":"), sort_keys=True).encode("utf-8"),
                    source_model_id=self.model_id,
                    source_model_version=version,
                ),
            ),
        )

    def _activate(self, job: JobEnvelope, artifacts: Mapping[str, ArtifactInput]) -> WorkerOutput:
        weights_id = str(job.payload.get("weights_artifact_id") or "")
        metadata_id = str(job.payload.get("metadata_artifact_id") or "")
        if weights_id not in artifacts or metadata_id not in artifacts:
            raise ValueError("model.activate requires weights and metadata artifacts")
        candidate = ResidualLinearModel.load(artifacts[weights_id].payload, artifacts[metadata_id].payload)
        if candidate.model_id != self.model_id:
            raise ValueError("activated bundle model_id does not match worker route")
        self._active = candidate
        return WorkerOutput(
            payload={
                "activated": True,
                "model_id": candidate.model_id,
                "model_version": candidate.version,
                "feature_schema_version": "features.v1",
            }
        )

