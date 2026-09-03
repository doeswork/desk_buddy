"""Create a self-contained, versioned dataset bundle for the training worker."""

from __future__ import annotations

import json
from io import BytesIO
from typing import Iterable

from ..contracts import FEATURE_SCHEMA, VisionObservationV1
from .features import CONTROL_INPUT_SIZE, ROTATION_INPUT_SIZE, feature_rows, provider_signature
from .storage import TrainingExample

DATASET_SCHEMA = "ik-training-dataset.v1"


def build_dataset_bundle(examples: Iterable[TrainingExample]) -> tuple[bytes, dict[str, object]]:
    import numpy as np

    records = tuple(examples)
    if len(records) < 2:
        raise ValueError("at least two reviewed successful captures are required")
    rotation_rows, control_rows, targets, capture_ids = [], [], [], []
    expected_provider: dict[str, str] | None = None
    for record in records:
        observation = VisionObservationV1.from_dict(record.observation)
        signature = provider_signature(observation)
        if expected_provider is None:
            expected_provider = signature
        elif signature != expected_provider:
            raise ValueError("training examples use incompatible detector/depth provider versions")
        rotation, control = feature_rows(observation)
        rotation_rows.append(rotation)
        control_rows.append(control)
        targets.append((record.rotation_deg, record.distance_mm, record.z_height_mm))
        capture_ids.append(record.capture_id)
    metadata: dict[str, object] = {
        "schema": DATASET_SCHEMA,
        "feature_schema": FEATURE_SCHEMA,
        "example_count": len(records),
        "rotation_input_size": ROTATION_INPUT_SIZE,
        "control_input_size": CONTROL_INPUT_SIZE,
        "provider": expected_provider or {},
    }
    output = BytesIO()
    np.savez_compressed(
        output,
        capture_ids=np.asarray(capture_ids, dtype="U160"),
        rotation=np.asarray(rotation_rows, dtype=np.float32),
        control=np.asarray(control_rows, dtype=np.float32),
        targets=np.asarray(targets, dtype=np.float32),
        metadata=np.frombuffer(json.dumps(metadata, separators=(",", ":")).encode("utf-8"), dtype=np.uint8),
    )
    return output.getvalue(), metadata


def read_dataset_bundle(payload: bytes) -> dict[str, object]:
    import numpy as np

    with np.load(BytesIO(payload), allow_pickle=False) as archive:
        metadata = json.loads(bytes(archive["metadata"].tolist()).decode("utf-8"))
        if metadata.get("schema") != DATASET_SCHEMA or metadata.get("feature_schema") != FEATURE_SCHEMA:
            raise ValueError("unsupported IK dataset schema")
        rotation = np.asarray(archive["rotation"], dtype=np.float32)
        control = np.asarray(archive["control"], dtype=np.float32)
        targets = np.asarray(archive["targets"], dtype=np.float32)
        capture_ids = np.asarray(archive["capture_ids"]).astype(str)
    count = len(capture_ids)
    if rotation.shape != (count, ROTATION_INPUT_SIZE):
        raise ValueError("invalid rotation feature matrix")
    if control.shape != (count, CONTROL_INPUT_SIZE):
        raise ValueError("invalid control feature matrix")
    if targets.shape != (count, 3):
        raise ValueError("invalid target matrix")
    if count < 2:
        raise ValueError("dataset must contain at least two captures")
    return {"metadata": metadata, "capture_ids": capture_ids, "rotation": rotation, "control": control, "targets": targets}
