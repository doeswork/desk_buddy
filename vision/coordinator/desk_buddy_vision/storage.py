from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from desk_buddy_vision_protocol import VisionRequest


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class OperationRecord:
    id: str
    request_id: str
    robot_id: str
    kind: str
    status: str
    stage: str
    request: dict[str, Any]
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    operation_id: str
    robot_id: str
    kind: str
    path: str
    mime_type: str
    byte_size: int
    sha256: str
    width: int | None
    height: int | None
    dtype: str | None
    model_id: str | None
    model_version: str | None
    metadata: dict[str, Any]
    created_at: str


ALLOWED_TRANSITIONS = {
    "accepted": {"waiting_for_photo", "processing", "planning", "completed", "failed", "cancelled"},
    "waiting_for_photo": {"processing", "failed", "cancelled"},
    "processing": {"planning", "completed", "failed", "cancelled"},
    "planning": {"executing", "completed", "failed", "cancelled"},
    "executing": {"completed", "failed"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}


class VisionStorage:
    """The coordinator's single authoritative SQLite metadata store."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
        self.migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def migrate(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS operations (
            id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL UNIQUE,
            robot_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            stage TEXT NOT NULL,
            source_sender TEXT NOT NULL,
            request_json TEXT NOT NULL,
            result_json TEXT,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS operations_robot_created_idx
            ON operations(robot_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artifact_id TEXT NOT NULL UNIQUE,
            operation_id TEXT NOT NULL REFERENCES operations(id),
            robot_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE,
            mime_type TEXT NOT NULL,
            byte_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            width INTEGER,
            height INTEGER,
            dtype TEXT,
            model_id TEXT,
            model_version TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS artifacts_operation_idx ON artifacts(operation_id, created_at);

        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_id TEXT NOT NULL UNIQUE REFERENCES operations(id),
            image_artifact_id TEXT NOT NULL,
            selected_detection_json TEXT,
            all_detections_json TEXT NOT NULL DEFAULT '[]',
            depth_artifact_id TEXT,
            feature_set_json TEXT,
            calibration_projection_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS calibration_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            robot_id TEXT NOT NULL,
            profile_id TEXT NOT NULL UNIQUE,
            version INTEGER NOT NULL,
            image_artifact_id TEXT NOT NULL,
            annotated_artifact_id TEXT NOT NULL,
            points_json TEXT NOT NULL,
            grid_json TEXT NOT NULL,
            reference_y_fraction REAL NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(robot_id, version)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS calibration_one_active_idx
            ON calibration_profiles(robot_id) WHERE is_active = 1;

        CREATE TABLE IF NOT EXISTS motion_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id TEXT NOT NULL UNIQUE,
            operation_id TEXT NOT NULL UNIQUE REFERENCES operations(id),
            planner_mode TEXT NOT NULL,
            planner_model_id TEXT,
            baseline_json TEXT NOT NULL,
            corrections_json TEXT NOT NULL,
            final_plan_json TEXT NOT NULL,
            validation_status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS command_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            motion_plan_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            command_action_id TEXT NOT NULL UNIQUE,
            action TEXT NOT NULL,
            request_json TEXT NOT NULL,
            status TEXT NOT NULL,
            response_json TEXT,
            published_at TEXT,
            completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS command_events_plan_idx
            ON command_events(motion_plan_id, step_index);

        CREATE TABLE IF NOT EXISTS service_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL UNIQUE,
            operation_id TEXT NOT NULL REFERENCES operations(id),
            request_id TEXT NOT NULL,
            service_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            model_id TEXT NOT NULL,
            status TEXT NOT NULL,
            request_json TEXT NOT NULL,
            result_json TEXT,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            published_at TEXT,
            completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS service_jobs_operation_idx ON service_jobs(operation_id, created_at);

        CREATE TABLE IF NOT EXISTS models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            version TEXT NOT NULL,
            artifact_id TEXT,
            service_id TEXT NOT NULL,
            feature_schema_version TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            is_active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(model_id, version)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS models_one_active_idx
            ON models(kind) WHERE is_active = 1;

        CREATE TABLE IF NOT EXISTS training_examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_id TEXT NOT NULL UNIQUE REFERENCES operations(id),
            feature_snapshot_artifact_id TEXT,
            success INTEGER,
            reviewed INTEGER NOT NULL DEFAULT 0,
            eligible INTEGER NOT NULL DEFAULT 0,
            target_rotation_delta_deg REAL,
            target_distance_delta_mm REAL,
            target_z_delta_mm REAL,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
        with self._lock, self._conn:
            self._conn.executescript(schema)
            columns = {str(row[1]) for row in self._conn.execute("PRAGMA table_info(operations)")}
            if "result_json" not in columns:
                self._conn.execute("ALTER TABLE operations ADD COLUMN result_json TEXT")

    def create_operation(self, request: VisionRequest, *, operation_id: str | None = None) -> tuple[OperationRecord, bool]:
        now = utc_now()
        identifier = operation_id or str(uuid.uuid4())
        with self.transaction() as conn:
            existing = conn.execute("SELECT * FROM operations WHERE request_id = ?", (request.request_id,)).fetchone()
            if existing:
                return self._operation(existing), False
            conn.execute(
                """
                INSERT INTO operations (
                    id, request_id, robot_id, kind, status, stage, source_sender,
                    request_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'accepted', 'request_validation', ?, ?, ?, ?)
                """,
                (
                    identifier,
                    request.request_id,
                    request.robot_id,
                    request.kind,
                    request.sender,
                    _json(request.as_dict()),
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM operations WHERE id = ?", (identifier,)).fetchone()
        assert row is not None
        return self._operation(row), True

    def get_operation(self, operation_id: str) -> OperationRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM operations WHERE id = ?", (operation_id,)).fetchone()
        return self._operation(row) if row else None

    def get_operation_by_request(self, request_id: str) -> OperationRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM operations WHERE request_id = ?", (request_id,)).fetchone()
        return self._operation(row) if row else None

    def update_operation(
        self,
        operation_id: str,
        *,
        status: str,
        stage: str,
        error_code: str | None = None,
        error_message: str | None = None,
        result: Mapping[str, Any] | None = None,
    ) -> OperationRecord:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM operations WHERE id = ?", (operation_id,)).fetchone()
            if not row:
                raise KeyError(operation_id)
            current = str(row["status"])
            if status != current and status not in ALLOWED_TRANSITIONS.get(current, set()):
                raise ValueError(f"invalid operation transition: {current} -> {status}")
            conn.execute(
                """
                UPDATE operations SET status = ?, stage = ?, error_code = ?, error_message = ?,
                    result_json = COALESCE(?, result_json), updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    stage,
                    error_code,
                    error_message,
                    _json(dict(result)) if result is not None else None,
                    utc_now(),
                    operation_id,
                ),
            )
            updated = conn.execute("SELECT * FROM operations WHERE id = ?", (operation_id,)).fetchone()
        assert updated is not None
        return self._operation(updated)

    def list_operations(self, *, robot_id: str, limit: int = 20) -> list[OperationRecord]:
        bounded_limit = min(max(int(limit), 1), 100)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM operations WHERE robot_id = ? ORDER BY created_at DESC LIMIT ?",
                (robot_id, bounded_limit),
            ).fetchall()
        return [self._operation(row) for row in rows]

    def recover_incomplete_operations(self) -> int:
        now = utc_now()
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE operations
                SET status = 'failed', stage = 'restart_recovery',
                    error_code = 'coordinator_restarted',
                    error_message = 'Operation state was uncertain after coordinator restart; physical commands were not retried.',
                    updated_at = ?
                WHERE status IN ('accepted', 'waiting_for_photo', 'processing', 'planning', 'executing')
                """,
                (now,),
            )
            conn.execute(
                """
                UPDATE service_jobs
                SET status = 'cancelled', error_code = 'coordinator_restarted',
                    error_message = 'Coordinator restarted before the worker result was committed.',
                    completed_at = ?
                WHERE status IN ('accepted', 'processing')
                """,
                (now,),
            )
            return int(cursor.rowcount)

    def register_artifact(self, record: ArtifactRecord) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, operation_id, robot_id, kind, path, mime_type,
                    byte_size, sha256, width, height, dtype, model_id,
                    model_version, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.artifact_id,
                    record.operation_id,
                    record.robot_id,
                    record.kind,
                    record.path,
                    record.mime_type,
                    record.byte_size,
                    record.sha256,
                    record.width,
                    record.height,
                    record.dtype,
                    record.model_id,
                    record.model_version,
                    _json(record.metadata),
                    record.created_at,
                ),
            )

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        return self._artifact(row) if row else None

    def create_service_job(self, job: Mapping[str, Any]) -> bool:
        with self.transaction() as conn:
            existing = conn.execute("SELECT 1 FROM service_jobs WHERE job_id = ?", (job["job_id"],)).fetchone()
            if existing:
                return False
            conn.execute(
                """
                INSERT INTO service_jobs (
                    job_id, operation_id, request_id, service_id, kind, model_id,
                    status, request_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'accepted', ?, ?)
                """,
                (
                    job["job_id"],
                    job["operation_id"],
                    job["request_id"],
                    job["service_id"],
                    job["kind"],
                    job["model_id"],
                    _json(job),
                    utc_now(),
                ),
            )
            return True

    def mark_job_published(self, job_id: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE service_jobs SET status = 'processing', published_at = COALESCE(published_at, ?) WHERE job_id = ?",
                (utc_now(), job_id),
            )

    def finish_service_job(self, job_id: str, result: Mapping[str, Any]) -> bool:
        status = str(result.get("status") or "failed")
        error = result.get("error") if isinstance(result.get("error"), dict) else {}
        with self.transaction() as conn:
            row = conn.execute("SELECT status FROM service_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return False
            if row["status"] in {"completed", "failed", "cancelled"}:
                return False
            conn.execute(
                """
                UPDATE service_jobs
                SET status = ?, result_json = ?, error_code = ?, error_message = ?, completed_at = ?
                WHERE job_id = ?
                """,
                (
                    status,
                    _json(result),
                    error.get("code"),
                    error.get("message"),
                    utc_now(),
                    job_id,
                ),
            )
            return True

    def get_service_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM service_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        result["result"] = json.loads(result.pop("result_json")) if result.get("result_json") else None
        return result

    def cancel_service_jobs(self, operation_id: str) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE service_jobs
                SET status = 'cancelled', error_code = 'operation_cancelled',
                    error_message = 'Parent operation was cancelled', completed_at = ?
                WHERE operation_id = ? AND status IN ('accepted', 'processing')
                """,
                (utc_now(), operation_id),
            )
            return int(cursor.rowcount)

    def save_observation(
        self,
        *,
        operation_id: str,
        image_artifact_id: str,
        selected_detection: Mapping[str, Any] | None,
        detections: list[Mapping[str, Any]],
        depth_artifact_id: str | None = None,
        feature_set: Mapping[str, Any] | None = None,
        calibration_projection: Mapping[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO observations (
                    operation_id, image_artifact_id, selected_detection_json,
                    all_detections_json, depth_artifact_id, feature_set_json,
                    calibration_projection_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(operation_id) DO UPDATE SET
                    image_artifact_id = excluded.image_artifact_id,
                    selected_detection_json = COALESCE(excluded.selected_detection_json, observations.selected_detection_json),
                    all_detections_json = CASE WHEN excluded.all_detections_json = '[]' THEN observations.all_detections_json ELSE excluded.all_detections_json END,
                    depth_artifact_id = COALESCE(excluded.depth_artifact_id, observations.depth_artifact_id),
                    feature_set_json = COALESCE(excluded.feature_set_json, observations.feature_set_json),
                    calibration_projection_json = COALESCE(excluded.calibration_projection_json, observations.calibration_projection_json),
                    updated_at = excluded.updated_at
                """,
                (
                    operation_id,
                    image_artifact_id,
                    _json(selected_detection) if selected_detection else None,
                    _json(detections),
                    depth_artifact_id,
                    _json(feature_set) if feature_set else None,
                    _json(calibration_projection) if calibration_projection else None,
                    now,
                    now,
                ),
            )

    def get_observation(self, operation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM observations WHERE operation_id = ?", (operation_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        for source, target in (
            ("selected_detection_json", "selected_detection"),
            ("all_detections_json", "all_detections"),
            ("feature_set_json", "feature_set"),
            ("calibration_projection_json", "calibration_projection"),
        ):
            value = result.pop(source)
            result[target] = json.loads(value) if value else None
        return result

    def active_calibration(self, robot_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM calibration_profiles WHERE robot_id = ? AND is_active = 1", (robot_id,)
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["points"] = json.loads(result.pop("points_json"))
        result["grid"] = json.loads(result.pop("grid_json"))
        result["is_active"] = bool(result["is_active"])
        return result

    def save_calibration(
        self,
        *,
        robot_id: str,
        image_artifact_id: str,
        annotated_artifact_id: str,
        points: Mapping[str, Any],
        grid: Mapping[str, Any],
        reference_y_fraction: float,
        activate: bool = True,
    ) -> str:
        profile_id = str(uuid.uuid4())
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM calibration_profiles WHERE robot_id = ?",
                (robot_id,),
            ).fetchone()
            version = int(row["version"])
            if activate:
                conn.execute("UPDATE calibration_profiles SET is_active = 0 WHERE robot_id = ?", (robot_id,))
            conn.execute(
                """
                INSERT INTO calibration_profiles (
                    robot_id, profile_id, version, image_artifact_id, annotated_artifact_id,
                    points_json, grid_json, reference_y_fraction, is_active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    robot_id,
                    profile_id,
                    version,
                    image_artifact_id,
                    annotated_artifact_id,
                    _json(points),
                    _json(grid),
                    float(reference_y_fraction),
                    1 if activate else 0,
                    utc_now(),
                ),
            )
        return profile_id

    def save_motion_plan(self, plan: Mapping[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO motion_plans (
                    plan_id, operation_id, planner_mode, planner_model_id,
                    baseline_json, corrections_json, final_plan_json,
                    validation_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan["plan_id"],
                    plan["operation_id"],
                    plan["planner_mode"],
                    plan.get("planner_model_id"),
                    _json(plan["baseline"]),
                    _json(plan["corrections"]),
                    _json(plan["final_target"]),
                    str(plan["safety"]["status"]),
                    utc_now(),
                ),
            )

    def upsert_training_example(
        self,
        *,
        operation_id: str,
        feature_snapshot_artifact_id: str | None = None,
        success: bool | None = None,
        reviewed: bool = False,
        eligible: bool = False,
        target_rotation_delta_deg: float | None = None,
        target_distance_delta_mm: float | None = None,
        target_z_delta_mm: float | None = None,
        notes: str = "",
    ) -> None:
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO training_examples (
                    operation_id, feature_snapshot_artifact_id, success, reviewed,
                    eligible, target_rotation_delta_deg, target_distance_delta_mm,
                    target_z_delta_mm, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(operation_id) DO UPDATE SET
                    feature_snapshot_artifact_id = COALESCE(excluded.feature_snapshot_artifact_id, training_examples.feature_snapshot_artifact_id),
                    success = excluded.success,
                    reviewed = excluded.reviewed,
                    eligible = excluded.eligible,
                    target_rotation_delta_deg = excluded.target_rotation_delta_deg,
                    target_distance_delta_mm = excluded.target_distance_delta_mm,
                    target_z_delta_mm = excluded.target_z_delta_mm,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    operation_id,
                    feature_snapshot_artifact_id,
                    None if success is None else int(success),
                    int(reviewed),
                    int(eligible),
                    target_rotation_delta_deg,
                    target_distance_delta_mm,
                    target_z_delta_mm,
                    notes,
                    now,
                    now,
                ),
            )

    def training_examples(
        self,
        *,
        robot_id: str | None = None,
        reviewed_only: bool = True,
        success_only: bool = False,
    ) -> list[dict[str, Any]]:
        where = ["t.eligible = 1"]
        values: list[Any] = []
        if reviewed_only:
            where.append("t.reviewed = 1")
        if success_only:
            where.append("t.success = 1")
        if robot_id:
            where.append("o.robot_id = ?")
            values.append(robot_id)
        query = f"""
            SELECT t.*, o.robot_id
            FROM training_examples t
            JOIN operations o ON o.id = t.operation_id
            WHERE {' AND '.join(where)}
            ORDER BY t.created_at
        """
        with self._lock:
            rows = self._conn.execute(query, values).fetchall()
        return [dict(row) for row in rows]

    def register_model(
        self,
        *,
        model_id: str,
        kind: str,
        version: str,
        artifact_id: str | None,
        service_id: str,
        feature_schema_version: str | None,
        metadata: Mapping[str, Any],
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO models (
                    model_id, kind, version, artifact_id, service_id,
                    feature_schema_version, metadata_json, is_active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(model_id, version) DO UPDATE SET
                    artifact_id = excluded.artifact_id,
                    service_id = excluded.service_id,
                    feature_schema_version = excluded.feature_schema_version,
                    metadata_json = excluded.metadata_json
                """,
                (
                    model_id,
                    kind,
                    version,
                    artifact_id,
                    service_id,
                    feature_schema_version,
                    _json(metadata),
                    utc_now(),
                ),
            )

    def get_model(self, model_id: str, version: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            if version:
                row = self._conn.execute(
                    "SELECT * FROM models WHERE model_id = ? AND version = ?", (model_id, version)
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM models WHERE model_id = ? ORDER BY is_active DESC, created_at DESC LIMIT 1",
                    (model_id,),
                ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        result["is_active"] = bool(result["is_active"])
        return result

    def list_models(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM models ORDER BY kind, model_id, created_at DESC").fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["metadata"] = json.loads(value.pop("metadata_json"))
            value["is_active"] = bool(value["is_active"])
            result.append(value)
        return result

    def activate_model(self, model_id: str, version: str) -> None:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT kind FROM models WHERE model_id = ? AND version = ?", (model_id, version)
            ).fetchone()
            if not row:
                raise KeyError(f"{model_id}:{version}")
            conn.execute("UPDATE models SET is_active = 0 WHERE kind = ?", (row["kind"],))
            conn.execute(
                "UPDATE models SET is_active = 1 WHERE model_id = ? AND version = ?", (model_id, version)
            )

    def register_command(self, *, motion_plan_id: str, step_index: int, action_id: str, action: str, request: Mapping[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO command_events (
                    motion_plan_id, step_index, command_action_id, action,
                    request_json, status, published_at
                ) VALUES (?, ?, ?, ?, ?, 'published', ?)
                """,
                (motion_plan_id, step_index, action_id, action, _json(request), utc_now()),
            )

    def finish_command(self, action_id: str, *, status: str, response: Mapping[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE command_events SET status = ?, response_json = ?,
                    completed_at = CASE WHEN ? IN ('completed', 'failed') THEN ? ELSE completed_at END
                WHERE command_action_id = ?
                """,
                (status, _json(response), status, utc_now(), action_id),
            )

    @staticmethod
    def _operation(row: sqlite3.Row) -> OperationRecord:
        return OperationRecord(
            id=str(row["id"]),
            request_id=str(row["request_id"]),
            robot_id=str(row["robot_id"]),
            kind=str(row["kind"]),
            status=str(row["status"]),
            stage=str(row["stage"]),
            request=json.loads(row["request_json"]),
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _artifact(row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=str(row["artifact_id"]),
            operation_id=str(row["operation_id"]),
            robot_id=str(row["robot_id"]),
            kind=str(row["kind"]),
            path=str(row["path"]),
            mime_type=str(row["mime_type"]),
            byte_size=int(row["byte_size"]),
            sha256=str(row["sha256"]),
            width=int(row["width"]) if row["width"] is not None else None,
            height=int(row["height"]) if row["height"] is not None else None,
            dtype=str(row["dtype"]) if row["dtype"] is not None else None,
            model_id=str(row["model_id"]) if row["model_id"] is not None else None,
            model_version=str(row["model_version"]) if row["model_version"] is not None else None,
            metadata=json.loads(row["metadata_json"]),
            created_at=str(row["created_at"]),
        )
