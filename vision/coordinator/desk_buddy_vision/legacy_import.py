from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from desk_buddy_vision_protocol import FeatureSet, VisionRequest

from .artifacts import ArtifactStore
from .storage import VisionStorage


@dataclass
class ImportStats:
    images_seen: int = 0
    images_imported: int = 0
    images_skipped: int = 0
    calibrations_imported: int = 0
    attempts_seen: int = 0
    attempts_imported: int = 0
    attempts_skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _value(row: sqlite3.Row, name: str, default: Any = None) -> Any:
    return row[name] if name in row.keys() and row[name] is not None else default


def _legacy_request_id(source: Path, table: str, row_id: Any) -> str:
    return f"legacy-{uuid.uuid5(uuid.NAMESPACE_URL, f'{source.resolve()}:{table}:{row_id}') }"


def _legacy_artifact_id(source: Path, table: str, row_id: Any) -> str:
    return f"legacy-artifact-{uuid.uuid5(uuid.NAMESPACE_OID, f'{source.resolve()}:{table}:{row_id}') }"


def _created_at(value: Any) -> str:
    if isinstance(value, (float, int)):
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(value or "").strip()
    return text or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_bbox(row: sqlite3.Row) -> tuple[float, float, float, float]:
    width = float(_value(row, "image_width", 0))
    height = float(_value(row, "image_height", 0))
    values = [float(_value(row, name, 0.0)) for name in ("bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1")]
    if not all(-0.05 <= value <= 1.05 for value in values):
        if width <= 0 or height <= 0:
            raise ValueError("legacy pixel bbox has no image dimensions")
        values = [values[0] / width, values[1] / height, values[2] / width, values[3] / height]
    x0, x1 = sorted((min(max(values[0], 0.0), 1.0), min(max(values[2], 0.0), 1.0)))
    y0, y1 = sorted((min(max(values[1], 0.0), 1.0), min(max(values[3], 0.0), 1.0)))
    return x0, y0, x1, y1


class LegacyImporter:
    def __init__(self, *, data_dir: Path, topic_map: Mapping[str, str], dry_run: bool = False) -> None:
        self.data_dir = data_dir.resolve()
        self.topic_map = dict(topic_map)
        self.dry_run = dry_run
        self.storage = VisionStorage(self.data_dir / "vision.sqlite3") if not dry_run else None
        self.artifacts = ArtifactStore(self.data_dir / "artifacts", self.storage) if self.storage else None
        self.stats = ImportStats()
        self._legacy_images: dict[tuple[Path, int], tuple[str, str]] = {}

    def close(self) -> None:
        if self.storage:
            self.storage.close()

    def import_vision_database(self, path: Path) -> None:
        source = path.resolve()
        connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            tables = _tables(connection)
            if "images" not in tables:
                raise ValueError("legacy vision database has no images table")
            for row in connection.execute("SELECT * FROM images ORDER BY id"):
                self._import_image(source, connection, row)
            if {"calibration_profiles", "calibration_grids"}.issubset(tables):
                self._import_calibrations(source, connection)
        finally:
            connection.close()

    def _import_image(self, source: Path, connection: sqlite3.Connection, row: sqlite3.Row) -> None:
        self.stats.images_seen += 1
        robot_id = self.topic_map.get(str(row["topic"]))
        payload = row["payload"]
        if not robot_id or not payload:
            self.stats.images_skipped += 1
            return
        self.stats.images_imported += 1
        if self.dry_run:
            self._legacy_images[(source, int(row["id"]))] = (
                "dry-run-operation",
                _legacy_artifact_id(source, "images", row["id"]),
            )
            return
        assert self.storage and self.artifacts
        photo_type = str(row["photo_type"])
        kind = "detect" if photo_type == "detect_object" else "calibration" if "calibrat" in photo_type else "photo"
        request = VisionRequest(
            request_id=_legacy_request_id(source, "images", row["id"]),
            robot_id=robot_id,
            sender="legacy_importer",
            kind=kind,
            created_at=_created_at(row["created_at"]),
            payload={"legacy_action_id": str(row["action_id"]), "phrase": str(row["phrase"] or "")},
        )
        operation, created = self.storage.create_operation(request)
        if not created:
            existing = self.storage.get_artifact(_legacy_artifact_id(source, "images", row["id"]))
            if existing:
                self._legacy_images[(source, int(row["id"]))] = (operation.id, existing.artifact_id)
            return
        artifact = self.artifacts.save_bytes(
            operation_id=operation.id,
            robot_id=robot_id,
            kind="original",
            payload=bytes(payload),
            mime_type="image/jpeg",
            artifact_id=_legacy_artifact_id(source, "images", row["id"]),
            metadata={"legacy_database": str(source), "legacy_image_id": row["id"]},
        )
        self._legacy_images[(source, int(row["id"]))] = (operation.id, artifact.artifact_id)
        detections = []
        if "detection_boxes" in _tables(connection):
            for detection in connection.execute(
                "SELECT * FROM detection_boxes WHERE image_id = ? ORDER BY score DESC", (row["id"],)
            ):
                detections.append(
                    {
                        "label": detection["label"],
                        "score": detection["score"],
                        "bbox_px": [detection[name] for name in ("x_min", "y_min", "x_max", "y_max")],
                        "legacy_depth_angle": detection["depth_x"],
                        "legacy_depth_distance_mm": detection["depth_y"],
                    }
                )
        if detections:
            self.storage.save_observation(
                operation_id=operation.id,
                image_artifact_id=artifact.artifact_id,
                selected_detection=detections[0],
                detections=detections,
            )
        self.storage.update_operation(operation.id, status="completed", stage="legacy_import")

    def _import_calibrations(self, source: Path, connection: sqlite3.Connection) -> None:
        grids = {
            str(row["topic"]): row
            for row in connection.execute("SELECT * FROM calibration_grids")
        }
        modifiers = {}
        if "y_depth_calibration_modifiers" in _tables(connection):
            modifiers = {
                str(row["topic"]): float(row["modifier"])
                for row in connection.execute("SELECT * FROM y_depth_calibration_modifiers")
            }
        for profile in connection.execute("SELECT * FROM calibration_profiles"):
            topic = str(profile["topic"])
            robot_id = self.topic_map.get(topic)
            grid_row = grids.get(topic)
            image = self._legacy_images.get((source, int(profile["image_id"]))) if profile["image_id"] else None
            if not robot_id or grid_row is None or image is None:
                continue
            annotated = (
                self._legacy_images.get((source, int(profile["annotated_image_id"])))
                if profile["annotated_image_id"]
                else image
            )
            if annotated is None:
                annotated = image
            self.stats.calibrations_imported += 1
            if self.dry_run:
                continue
            assert self.storage
            self.storage.save_calibration(
                robot_id=robot_id,
                image_artifact_id=image[1],
                annotated_artifact_id=annotated[1],
                points=json.loads(profile["points_json"]),
                grid=json.loads(grid_row["grid_json"]),
                reference_y_fraction=float(modifiers.get(topic, 0.1)),
                activate=True,
            )

    def import_grab_attempts(self, path: Path) -> None:
        source = path.resolve()
        connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            if "grab_attempts" not in _tables(connection):
                raise ValueError("legacy web database has no grab_attempts table")
            columns = _columns(connection, "grab_attempts")
            required = {
                "depth_patch",
                "bbox_x0",
                "bbox_y0",
                "bbox_x1",
                "bbox_y1",
                "image_width",
                "image_height",
                "confidence",
                "reviewed_for_ml",
                "target_y_offset_mm",
                "target_rotation_adjust_deg",
            }
            if not required.issubset(columns):
                missing = ", ".join(sorted(required - columns))
                raise ValueError(f"grab_attempts is missing migration fields: {missing}")
            for row in connection.execute("SELECT * FROM grab_attempts ORDER BY id"):
                self._import_attempt(source, row)
        finally:
            connection.close()

    def _import_attempt(self, source: Path, row: sqlite3.Row) -> None:
        self.stats.attempts_seen += 1
        robot_id = self.topic_map.get(str(_value(row, "topic", "")))
        patch_bytes = _value(row, "depth_patch")
        reviewed = bool(_value(row, "reviewed_for_ml", False))
        if not robot_id or not patch_bytes or not reviewed:
            self.stats.attempts_skipped += 1
            return
        try:
            patch = np.frombuffer(bytes(patch_bytes), dtype=np.float16)
            if patch.size != 4096:
                raise ValueError("depth patch size")
            patch = patch.astype(np.float32)
            bbox = _normalize_bbox(row)
            width = int(_value(row, "image_width", 0))
            height = int(_value(row, "image_height", 0))
            confidence = float(_value(row, "confidence", 0.0))
            commanded_rotation = float(_value(row, "commanded_rotation_value", 0.0))
            if str(_value(row, "commanded_rotation_direction", "RIGHT")).upper() == "LEFT":
                commanded_rotation *= -1.0
            commanded_distance = float(_value(row, "commanded_ik_distance_mm", _value(row, "distance_mm", 0.0)))
            commanded_z = float(_value(row, "commanded_ik_z_height_mm", 0.0))
            target_rotation = float(_value(row, "target_rotation_adjust_deg", 0.0))
            target_distance = float(_value(row, "target_y_offset_mm", 0.0))
            observed_z = _value(row, "z_height_mm")
            target_z = float(observed_z) - commanded_z if observed_z is not None else 0.0
        except Exception:
            self.stats.attempts_skipped += 1
            return
        x0, y0, x1, y1 = bbox
        percentiles = tuple(float(value) for value in np.percentile(patch, [5, 25, 50, 75, 95]))
        features = FeatureSet(
            image_width=width,
            image_height=height,
            phrase=str(_value(row, "phrase", "")),
            selected_label=str(_value(row, "label", "")),
            detection_score=confidence,
            bbox_norm=bbox,
            bbox_center_norm=((x0 + x1) / 2.0, (y0 + y1) / 2.0),
            bbox_area_norm=(x1 - x0) * (y1 - y0),
            depth_patch_64x64=tuple(float(value) for value in patch),
            depth_mean=float(np.mean(patch)),
            depth_median=float(np.median(patch)),
            depth_std=float(np.std(patch)),
            depth_min=float(np.min(patch)),
            depth_max=float(np.max(patch)),
            depth_percentiles=percentiles,
            calibration_angle_deg=commanded_rotation,
            calibration_distance_mm=commanded_distance,
            calibration_z_height_mm=commanded_z,
            calibration_zone="legacy",
            calibration_extrapolated=False,
            baseline_rotation_deg=commanded_rotation,
            baseline_controlik_distance_mm=commanded_distance,
            baseline_controlik_z_height_mm=commanded_z,
        )
        self.stats.attempts_imported += 1
        if self.dry_run:
            return
        assert self.storage and self.artifacts
        request = VisionRequest(
            request_id=_legacy_request_id(source, "grab_attempts", row["id"]),
            robot_id=robot_id,
            sender="legacy_importer",
            kind="detect",
            created_at=_created_at(_value(row, "created_at")),
            payload={"legacy_attempt_id": str(_value(row, "attempt_id", row["id"])), "execute": False},
        )
        operation, created = self.storage.create_operation(request)
        if not created:
            return
        snapshot_payload = {
            "features": features.as_dict(),
            "targets": {
                "rotation_delta_deg": target_rotation,
                "distance_delta_mm": target_distance,
                "z_height_delta_mm": target_z,
            },
            "migration": {
                "source_database": str(source),
                "source_table": "grab_attempts",
                "source_id": row["id"],
                "z_delta_inferred": observed_z is not None,
            },
        }
        artifact = self.artifacts.save_bytes(
            operation_id=operation.id,
            robot_id=robot_id,
            kind="training_feature_snapshot",
            payload=json.dumps(snapshot_payload, separators=(",", ":"), sort_keys=True).encode("utf-8"),
            mime_type="application/json",
            artifact_id=_legacy_artifact_id(source, "grab_attempts", row["id"]),
            metadata={"feature_schema_version": "features.v1"},
        )
        self.storage.upsert_training_example(
            operation_id=operation.id,
            feature_snapshot_artifact_id=artifact.artifact_id,
            success=bool(row["success"]) if _value(row, "success") is not None else None,
            reviewed=True,
            eligible=True,
            target_rotation_delta_deg=target_rotation,
            target_distance_delta_mm=target_distance,
            target_z_delta_mm=target_z,
            notes=str(_value(row, "ml_notes", _value(row, "notes", ""))),
        )
        self.storage.update_operation(operation.id, status="completed", stage="legacy_import")


def _topic_map(values: list[str]) -> dict[str, str]:
    result = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--topic-map must use LEGACY_TOPIC=ROBOT_ID")
        topic, robot_id = value.split("=", 1)
        if not topic or not robot_id:
            raise ValueError("--topic-map requires non-empty topic and robot ID")
        result[topic] = robot_id
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Import legacy Desk Buddy vision and reviewed training data")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--legacy-vision-db")
    parser.add_argument("--legacy-web-db")
    parser.add_argument("--topic-map", action="append", default=[], metavar="LEGACY_TOPIC=ROBOT_ID")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.legacy_vision_db and not args.legacy_web_db:
        parser.error("at least one legacy database is required")
    importer = LegacyImporter(
        data_dir=Path(args.data_dir),
        topic_map=_topic_map(args.topic_map),
        dry_run=args.dry_run,
    )
    try:
        if args.legacy_vision_db:
            importer.import_vision_database(Path(args.legacy_vision_db))
        if args.legacy_web_db:
            importer.import_grab_attempts(Path(args.legacy_web_db))
        print(json.dumps(importer.stats.as_dict(), indent=2, sort_keys=True))
    finally:
        importer.close()


if __name__ == "__main__":
    main()
