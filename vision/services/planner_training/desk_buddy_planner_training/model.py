from __future__ import annotations

import json
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Mapping

import numpy as np


VECTOR_SCHEMA = "residual-vector.v1"
OUTPUT_NAMES = ("rotation_delta_deg", "distance_delta_mm", "z_height_delta_mm")


def feature_vector(features: Mapping[str, Any]) -> np.ndarray:
    if features.get("schema") != "features.v1":
        raise ValueError("planner requires features.v1")
    patch = np.asarray(features["depth_patch_64x64"], dtype=np.float32)
    if patch.size != 4096:
        raise ValueError("depth_patch_64x64 must have 4096 values")
    patch = patch.reshape(64, 64).reshape(8, 8, 8, 8).mean(axis=(1, 3)).reshape(-1)
    bbox = np.asarray(features["bbox_norm"], dtype=np.float32)
    percentiles = np.asarray(features.get("depth_percentiles", []), dtype=np.float32)
    scalar_names = (
        "detection_score",
        "bbox_area_norm",
        "depth_mean",
        "depth_median",
        "depth_std",
        "depth_min",
        "depth_max",
        "calibration_angle_deg",
        "calibration_distance_mm",
        "calibration_z_height_mm",
        "baseline_rotation_deg",
        "baseline_controlik_distance_mm",
        "baseline_controlik_z_height_mm",
    )
    scalars = np.asarray([float(features.get(name, 0.0)) for name in scalar_names], dtype=np.float32)
    result = np.concatenate((patch, bbox, percentiles, scalars)).astype(np.float32)
    if not np.all(np.isfinite(result)):
        raise ValueError("feature vector contains non-finite values")
    return result


@dataclass(frozen=True)
class ResidualLinearModel:
    model_id: str
    version: str
    mean: np.ndarray
    scale: np.ndarray
    weights: np.ndarray
    bias: np.ndarray

    def predict(self, features: Mapping[str, Any]) -> dict[str, float]:
        vector = feature_vector(features)
        if vector.shape != self.mean.shape:
            raise ValueError("feature vector shape does not match model")
        normalized = (vector - self.mean) / self.scale
        values = normalized @ self.weights + self.bias
        return {name: float(value) for name, value in zip(OUTPUT_NAMES, values)}

    def weights_bytes(self) -> bytes:
        output = BytesIO()
        np.savez_compressed(
            output,
            mean=self.mean,
            scale=self.scale,
            weights=self.weights,
            bias=self.bias,
        )
        return output.getvalue()

    def metadata(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "version": self.version,
            "model_type": "ridge_residual",
            "feature_schema_version": "features.v1",
            "vector_schema_version": VECTOR_SCHEMA,
            "output_schema_version": "plan-corrections.v1",
            "output_names": list(OUTPUT_NAMES),
            "input_size": int(self.mean.size),
        }

    @classmethod
    def load(cls, weights_payload: bytes, metadata_payload: bytes) -> "ResidualLinearModel":
        metadata = json.loads(metadata_payload.decode("utf-8"))
        if metadata.get("feature_schema_version") != "features.v1" or metadata.get("vector_schema_version") != VECTOR_SCHEMA:
            raise ValueError("model feature schema is incompatible")
        with np.load(BytesIO(weights_payload), allow_pickle=False) as values:
            model = cls(
                model_id=str(metadata["model_id"]),
                version=str(metadata["version"]),
                mean=np.asarray(values["mean"], dtype=np.float32),
                scale=np.asarray(values["scale"], dtype=np.float32),
                weights=np.asarray(values["weights"], dtype=np.float32),
                bias=np.asarray(values["bias"], dtype=np.float32),
            )
        if model.weights.shape != (model.mean.size, len(OUTPUT_NAMES)) or model.bias.shape != (len(OUTPUT_NAMES),):
            raise ValueError("model weight dimensions are invalid")
        return model


def train_ridge(
    examples: list[Mapping[str, Any]],
    *,
    model_id: str,
    version: str,
    seed: int,
    validation_split: float,
    regularization: float,
) -> tuple[ResidualLinearModel, dict[str, Any]]:
    if len(examples) < 3:
        raise ValueError("at least three reviewed examples are required")
    x = np.stack([feature_vector(example["features"]) for example in examples]).astype(np.float64)
    y = np.asarray(
        [[float(example["targets"][name]) for name in OUTPUT_NAMES] for example in examples], dtype=np.float64
    )
    generator = np.random.default_rng(seed)
    indexes = generator.permutation(len(examples))
    validation_count = min(len(examples) - 2, max(1, int(round(len(examples) * validation_split))))
    validation_indexes = indexes[:validation_count]
    training_indexes = indexes[validation_count:]
    x_train = x[training_indexes]
    y_train = y[training_indexes]
    mean = x_train.mean(axis=0)
    scale = x_train.std(axis=0)
    scale[scale < 1e-6] = 1.0
    normalized = (x_train - mean) / scale
    augmented = np.column_stack((normalized, np.ones(len(normalized))))
    penalty = np.eye(augmented.shape[1], dtype=np.float64) * float(regularization)
    penalty[-1, -1] = 0.0
    coefficients = np.linalg.solve(augmented.T @ augmented + penalty, augmented.T @ y_train)
    weights = coefficients[:-1]
    bias = coefficients[-1]
    model = ResidualLinearModel(
        model_id=model_id,
        version=version,
        mean=mean.astype(np.float32),
        scale=scale.astype(np.float32),
        weights=weights.astype(np.float32),
        bias=bias.astype(np.float32),
    )
    validation_predictions = np.stack([list(model.predict(examples[index]["features"]).values()) for index in validation_indexes])
    validation_targets = y[validation_indexes]
    metrics = {
        "training_examples": int(len(training_indexes)),
        "validation_examples": int(len(validation_indexes)),
        "seed": int(seed),
        "validation_split": float(validation_split),
        "regularization": float(regularization),
        "mae": {
            name: float(value)
            for name, value in zip(OUTPUT_NAMES, np.mean(np.abs(validation_predictions - validation_targets), axis=0))
        },
        "mse": {
            name: float(value)
            for name, value in zip(OUTPUT_NAMES, np.mean((validation_predictions - validation_targets) ** 2, axis=0))
        },
        "training_indexes": [int(index) for index in training_indexes],
        "validation_indexes": [int(index) for index in validation_indexes],
    }
    return model, metrics

