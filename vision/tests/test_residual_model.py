from __future__ import annotations

import unittest

from tests.support import ROOT

from desk_buddy_planner_training.model import ResidualLinearModel, train_ridge
from desk_buddy_planner_training.handler import ResidualPlannerHandler
from desk_buddy_vision_protocol import ArtifactInput, JobEnvelope


def features(value: float) -> dict:
    return {
        "schema": "features.v1",
        "depth_patch_64x64": [value] * 4096,
        "bbox_norm": [0.1, 0.2, 0.3, 0.4],
        "depth_percentiles": [value] * 5,
        "detection_score": 0.9,
        "bbox_area_norm": 0.04,
        "depth_mean": value,
        "depth_median": value,
        "depth_std": 0.0,
        "depth_min": value,
        "depth_max": value,
        "calibration_angle_deg": value * 10,
        "calibration_distance_mm": value * 100,
        "calibration_z_height_mm": 0,
        "baseline_rotation_deg": value * 10,
        "baseline_controlik_distance_mm": value * 100,
        "baseline_controlik_z_height_mm": 0,
    }


class ResidualModelTests(unittest.TestCase):
    def test_reproducible_train_save_reload_predict(self):
        examples = [
            {
                "features": features(index / 10),
                "targets": {
                    "rotation_delta_deg": index,
                    "distance_delta_mm": index * 2,
                    "z_height_delta_mm": -index,
                },
            }
            for index in range(10)
        ]
        model, metrics = train_ridge(
            examples,
            model_id="ik-residual-v1",
            version="test-1",
            seed=42,
            validation_split=0.2,
            regularization=0.01,
        )
        loaded = ResidualLinearModel.load(
            model.weights_bytes(),
            __import__("json").dumps(model.metadata()).encode("utf-8"),
        )
        self.assertEqual(loaded.version, "test-1")
        self.assertEqual(metrics["validation_examples"], 2)
        self.assertEqual(model.predict(features(0.5)), loaded.predict(features(0.5)))

    def test_training_requires_explicit_activation_before_prediction(self):
        handler = ResidualPlannerHandler(model_id="ik-residual-v1")
        feature_artifacts = {}
        manifest_rows = []
        for index in range(5):
            artifact_id = f"feature-{index}"
            value = {
                "features": features(index / 10),
                "targets": {
                    "rotation_delta_deg": index,
                    "distance_delta_mm": index * 2,
                    "z_height_delta_mm": -index,
                },
            }
            feature_artifacts[artifact_id] = ArtifactInput(
                artifact_id=artifact_id,
                payload=__import__("json").dumps(value).encode("utf-8"),
                metadata={},
            )
            manifest_rows.append({"operation_id": f"op-{index}", "feature_artifact_id": artifact_id})
        manifest = {
            "schema": "training-manifest.v1",
            "feature_schema_version": "features.v1",
            "examples": manifest_rows,
            "seed": 42,
            "validation_split": 0.2,
        }
        inputs = {
            "manifest": ArtifactInput(
                artifact_id="manifest",
                payload=__import__("json").dumps(manifest).encode("utf-8"),
                metadata={},
            ),
            **feature_artifacts,
        }
        training_job = JobEnvelope(
            request_id="request",
            operation_id="training-operation",
            job_id="training-job",
            service_id="planner",
            kind="training.start",
            model_id="ik-residual-v1",
            input_artifact_ids=tuple(inputs),
            payload={"manifest_artifact_id": "manifest", "planner_model_name": "test"},
        )
        output = handler.handle(training_job, inputs)
        generated = {artifact.role: artifact for artifact in output.artifacts}
        self.assertTrue(output.payload["activation_required"])
        with self.assertRaisesRegex(RuntimeError, "not active"):
            handler.handle(
                JobEnvelope(
                    request_id="request",
                    operation_id="operation",
                    job_id="predict-before-activate",
                    service_id="planner",
                    kind="residual.predict",
                    model_id="ik-residual-v1",
                    payload={"features": features(0.2)},
                ),
                {},
            )
        activation_inputs = {
            "weights": ArtifactInput("weights", generated["model_weights"].payload, {}),
            "metadata": ArtifactInput("metadata", generated["model_metadata"].payload, {}),
        }
        activation = handler.handle(
            JobEnvelope(
                request_id="request",
                operation_id="activation-operation",
                job_id="activation-job",
                service_id="planner",
                kind="model.activate",
                model_id="ik-residual-v1",
                input_artifact_ids=("weights", "metadata"),
                payload={"weights_artifact_id": "weights", "metadata_artifact_id": "metadata"},
            ),
            activation_inputs,
        )
        self.assertTrue(activation.payload["activated"])
        prediction = handler.handle(
            JobEnvelope(
                request_id="request",
                operation_id="operation",
                job_id="prediction-job",
                service_id="planner",
                kind="residual.predict",
                model_id="ik-residual-v1",
                payload={"features": features(0.2)},
            ),
            {},
        )
        self.assertEqual(prediction.payload["output_schema"], "plan-corrections.v1")


if __name__ == "__main__":
    unittest.main()
