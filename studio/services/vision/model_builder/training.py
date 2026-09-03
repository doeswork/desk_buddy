"""MQTT job handler for isolated custom IK model training."""

from __future__ import annotations

import hashlib
import random
from io import BytesIO
from typing import Mapping

from ..contracts import FEATURE_SCHEMA, JobEnvelope, utc_now
from ..frames import BinaryArtifact
from ..worker import GeneratedArtifact, WorkerOutput
from .dataset import DATASET_SCHEMA, read_dataset_bundle
from .network import MODEL_ARCHITECTURE, build_model

MODEL_BUNDLE_SCHEMA = "ik-model-bundle.v1"


class IKTrainingHandler:
    model_id = "ik-mlp-builder"
    model_version = "1"
    job_kinds = ("ik_model.train",)
    input_schemas = (DATASET_SCHEMA,)
    output_schemas = (MODEL_BUNDLE_SCHEMA,)

    def __init__(self, *, device: str = "cpu", epochs: int = 250, seed: int = 4080) -> None:
        self.device = device
        self.epochs = max(1, int(epochs))
        self.seed = int(seed)

    def handle(self, job: JobEnvelope, artifacts: Mapping[str, BinaryArtifact]) -> WorkerOutput:
        import numpy as np
        import torch

        dataset_artifact = artifacts.get("dataset")
        if dataset_artifact is None:
            raise ValueError("training requires the dataset artifact")
        dataset = read_dataset_bundle(dataset_artifact.payload)
        capture_ids = dataset["capture_ids"]
        unique_ids = sorted(set(str(value) for value in capture_ids))
        if len(unique_ids) < 2:
            raise ValueError("training and validation require at least two capture IDs")
        random.Random(self.seed).shuffle(unique_ids)
        validation_count = max(1, round(len(unique_ids) * 0.2))
        validation_ids = set(unique_ids[:validation_count])
        validation_mask = np.asarray([str(value) in validation_ids for value in capture_ids], dtype=bool)
        training_mask = ~validation_mask
        if not training_mask.any():
            raise ValueError("capture split left no training examples")

        torch.manual_seed(self.seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(self.seed)
        device = torch.device(self.device)
        model = build_model().to(device)
        rotation = torch.from_numpy(dataset["rotation"]).to(device)
        control = torch.from_numpy(dataset["control"]).to(device)
        targets = torch.from_numpy(dataset["targets"]).to(device)
        limits = {"rotation": 45.0, "distance": 180.0, "z_min": -30.0, "z_max": 120.0}
        normalized = torch.empty_like(targets)
        normalized[:, 0] = targets[:, 0] / limits["rotation"]
        normalized[:, 1] = targets[:, 1] / limits["distance"]
        normalized[:, 2] = (targets[:, 2] - limits["z_min"]) / (limits["z_max"] - limits["z_min"])
        train_indices = torch.from_numpy(np.flatnonzero(training_mask)).to(device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=float(job.payload.get("learning_rate", 0.002)))
        loss_function = torch.nn.SmoothL1Loss()
        model.train()
        for _ in range(int(job.payload.get("epochs", self.epochs))):
            optimizer.zero_grad()
            predicted_rotation, predicted_control = model(rotation[train_indices], control[train_indices])
            loss = loss_function(torch.cat((predicted_rotation, predicted_control), dim=1), normalized[train_indices])
            loss.backward()
            optimizer.step()

        model.eval()
        validation_indices = torch.from_numpy(np.flatnonzero(validation_mask)).to(device)
        with torch.no_grad():
            prediction_rotation, prediction_control = model(rotation[validation_indices], control[validation_indices])
            prediction = torch.cat((prediction_rotation, prediction_control), dim=1)
            physical = torch.empty_like(prediction)
            physical[:, 0] = prediction[:, 0] * limits["rotation"]
            physical[:, 1] = prediction[:, 1] * limits["distance"]
            physical[:, 2] = prediction[:, 2] * (limits["z_max"] - limits["z_min"]) + limits["z_min"]
            mae = torch.mean(torch.abs(physical - targets[validation_indices]), dim=0).cpu().tolist()

        requested_name = str(job.payload.get("model_name") or "custom-ik").strip()
        revision = hashlib.sha256(dataset_artifact.payload).hexdigest()[:12]
        trained_model_id = f"{requested_name}-{revision}"
        metadata = {
            "schema": MODEL_BUNDLE_SCHEMA,
            "model_id": trained_model_id,
            "model_version": revision,
            "architecture": MODEL_ARCHITECTURE,
            "feature_schema": FEATURE_SCHEMA,
            "provider": dataset["metadata"]["provider"],
            "metrics": {
                "validation_mae_rotation_deg": float(mae[0]),
                "validation_mae_distance_mm": float(mae[1]),
                "validation_mae_z_height_mm": float(mae[2]),
                "training_captures": int(training_mask.sum()),
                "validation_captures": int(validation_mask.sum()),
            },
            "limits": limits,
            "seed": self.seed,
            "trained_at": utc_now(),
        }
        bundle = BytesIO()
        torch.save({"state_dict": model.state_dict(), "metadata": metadata}, bundle)
        return WorkerOutput(
            payload={"model": metadata},
            artifacts=(GeneratedArtifact(
                role="model", mime_type="application/x-pytorch", payload=bundle.getvalue(),
                artifact_id=f"{trained_model_id}-checkpoint", metadata=metadata,
            ),),
        )
