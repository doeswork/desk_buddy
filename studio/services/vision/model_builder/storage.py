"""Studio-owned vision records; workers never access this database or its paths."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..contracts import IKTargetV1, VisionObservationV1, utc_now
from ..frames import digest

SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,160}$")


class ArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, artifact_id: str, payload: bytes, suffix: str = ".bin") -> Path:
        if not SAFE_ID.fullmatch(artifact_id):
            raise ValueError("artifact_id contains unsafe characters")
        if not suffix.startswith(".") or "/" in suffix or "\\" in suffix:
            raise ValueError("artifact suffix is invalid")
        target = self.root / f"{artifact_id}{suffix}"
        if target.exists():
            existing = target.read_bytes()
            if digest(existing) != digest(payload):
                raise ValueError(f"artifact {artifact_id} already exists with different bytes")
            return target
        handle, temporary = tempfile.mkstemp(prefix=f".{artifact_id}-", dir=self.root)
        try:
            with os.fdopen(handle, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            Path(temporary).replace(target)
        finally:
            candidate = Path(temporary)
            if candidate.exists():
                candidate.unlink()
        return target

    def get(self, artifact_id: str) -> bytes:
        matches = list(self.root.glob(f"{artifact_id}.*"))
        if len(matches) != 1:
            raise FileNotFoundError(artifact_id)
        return matches[0].read_bytes()


@dataclass(frozen=True)
class TrainingExample:
    capture_id: str
    robot_id: str
    observation: dict[str, Any]
    rotation_deg: float
    distance_mm: float
    z_height_mm: float


class VisionStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifacts = ArtifactStore(self.root / "artifacts")
        self.database_path = self.root / "vision.sqlite3"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS capture (
                    capture_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    robot_id TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL,
                    original_artifact_id TEXT NOT NULL,
                    detection_json TEXT,
                    depth_json TEXT,
                    observation_json TEXT,
                    deterministic_target_json TEXT,
                    learned_target_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS review (
                    capture_id TEXT PRIMARY KEY REFERENCES capture(capture_id) ON DELETE CASCADE,
                    disposition TEXT NOT NULL CHECK(disposition IN ('successful','failed','discarded')),
                    rotation_deg REAL,
                    distance_mm REAL,
                    z_height_mm REAL,
                    notes TEXT NOT NULL DEFAULT '',
                    reviewed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ik_model (
                    model_id TEXT PRIMARY KEY,
                    model_version TEXT NOT NULL,
                    robot_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS calibration_profile (
                    robot_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    points_json TEXT NOT NULL,
                    grid_json TEXT NOT NULL,
                    annotated_artifact_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS capture_robot_created ON capture(robot_id, created_at);
                CREATE INDEX IF NOT EXISTS model_robot_active ON ik_model(robot_id, active);
                """
            )

    def save_calibration(
        self,
        *,
        robot_id: str,
        profile_id: str,
        points: Mapping[str, Any],
        grid: Mapping[str, Any],
        annotated_artifact_id: str,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO calibration_profile(
                       robot_id, profile_id, points_json, grid_json, annotated_artifact_id, created_at
                   ) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(robot_id) DO UPDATE SET
                       profile_id=excluded.profile_id, points_json=excluded.points_json,
                       grid_json=excluded.grid_json, annotated_artifact_id=excluded.annotated_artifact_id,
                       created_at=excluded.created_at""",
                (robot_id, profile_id, json.dumps(dict(points)), json.dumps(dict(grid)), annotated_artifact_id, utc_now()),
            )

    def calibration(self, robot_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM calibration_profile WHERE robot_id=?", (robot_id,)).fetchone()
        if row is None:
            return None
        return {
            "robot_id": row["robot_id"], "profile_id": row["profile_id"],
            "points": json.loads(row["points_json"]), "grid": json.loads(row["grid_json"]),
            "annotated_artifact_id": row["annotated_artifact_id"], "created_at": row["created_at"],
        }

    def begin_capture(
        self,
        *,
        capture_id: str,
        request_id: str,
        robot_id: str,
        prompt: str,
        original_artifact_id: str,
    ) -> None:
        now = utc_now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO capture(
                    capture_id, request_id, robot_id, prompt, status,
                    original_artifact_id, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (capture_id, request_id, robot_id, prompt, "processing", original_artifact_id, now, now),
            )

    def update_capture(self, capture_id: str, **values: Any) -> None:
        allowed = {
            "status",
            "detection_json",
            "depth_json",
            "observation_json",
            "deterministic_target_json",
            "learned_target_json",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unsupported capture columns: {sorted(unknown)}")
        assignments = []
        parameters = []
        for key, value in values.items():
            assignments.append(f"{key} = ?")
            parameters.append(value if isinstance(value, str) or value is None else json.dumps(value, separators=(",", ":")))
        assignments.append("updated_at = ?")
        parameters.extend((utc_now(), capture_id))
        with self._connect() as db:
            db.execute(f"UPDATE capture SET {', '.join(assignments)} WHERE capture_id = ?", parameters)

    def review(
        self,
        capture_id: str,
        *,
        disposition: str,
        rotation_deg: float | None = None,
        distance_mm: float | None = None,
        z_height_mm: float | None = None,
        notes: str = "",
    ) -> None:
        if disposition not in {"successful", "failed", "discarded"}:
            raise ValueError("invalid review disposition")
        if disposition == "successful" and None in (rotation_deg, distance_mm, z_height_mm):
            raise ValueError("successful reviews require rotation, distance, and z-height")
        with self._connect() as db:
            db.execute(
                """INSERT INTO review(
                    capture_id, disposition, rotation_deg, distance_mm, z_height_mm, notes, reviewed_at
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(capture_id) DO UPDATE SET
                    disposition=excluded.disposition,
                    rotation_deg=excluded.rotation_deg,
                    distance_mm=excluded.distance_mm,
                    z_height_mm=excluded.z_height_mm,
                    notes=excluded.notes,
                    reviewed_at=excluded.reviewed_at""",
                (capture_id, disposition, rotation_deg, distance_mm, z_height_mm, notes, utc_now()),
            )

    def training_examples(self, robot_id: str) -> tuple[TrainingExample, ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT c.capture_id, c.robot_id, c.observation_json,
                          r.rotation_deg, r.distance_mm, r.z_height_mm
                   FROM capture c JOIN review r USING(capture_id)
                   WHERE c.robot_id=? AND r.disposition='successful'
                         AND c.observation_json IS NOT NULL
                   ORDER BY c.created_at""",
                (robot_id,),
            ).fetchall()
        return tuple(
            TrainingExample(
                capture_id=row["capture_id"],
                robot_id=row["robot_id"],
                observation=json.loads(row["observation_json"]),
                rotation_deg=float(row["rotation_deg"]),
                distance_mm=float(row["distance_mm"]),
                z_height_mm=float(row["z_height_mm"]),
            )
            for row in rows
        )

    def save_model(
        self,
        *,
        model_id: str,
        model_version: str,
        robot_id: str,
        artifact_id: str,
        metadata: Mapping[str, Any],
    ) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO ik_model(model_id, model_version, robot_id, artifact_id, metadata_json, active, created_at)
                   VALUES(?,?,?,?,?,0,?)
                   ON CONFLICT(model_id) DO UPDATE SET
                       model_version=excluded.model_version,
                       robot_id=excluded.robot_id,
                       artifact_id=excluded.artifact_id,
                       metadata_json=excluded.metadata_json,
                       active=0,
                       created_at=excluded.created_at""",
                (model_id, model_version, robot_id, artifact_id, json.dumps(dict(metadata)), utc_now()),
            )

    def activate_model(self, robot_id: str, model_id: str) -> None:
        with self._connect() as db:
            row = db.execute("SELECT 1 FROM ik_model WHERE robot_id=? AND model_id=?", (robot_id, model_id)).fetchone()
            if row is None:
                raise ValueError(f"model {model_id} does not belong to robot {robot_id}")
            db.execute("UPDATE ik_model SET active=0 WHERE robot_id=?", (robot_id,))
            db.execute("UPDATE ik_model SET active=1 WHERE robot_id=? AND model_id=?", (robot_id, model_id))

    def models(self, robot_id: str | None = None) -> tuple[dict[str, Any], ...]:
        query = "SELECT * FROM ik_model"
        params: tuple[Any, ...] = ()
        if robot_id is not None:
            query += " WHERE robot_id=?"
            params = (robot_id,)
        query += " ORDER BY created_at DESC"
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return tuple(
            {
                "model_id": row["model_id"],
                "model_version": row["model_version"],
                "robot_id": row["robot_id"],
                "artifact_id": row["artifact_id"],
                "metadata": json.loads(row["metadata_json"]),
                "active": bool(row["active"]),
                "created_at": row["created_at"],
            }
            for row in rows
        )

    def model(self, model_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM ik_model WHERE model_id=?", (model_id,)).fetchone()
        if row is None:
            return None
        return {
            "model_id": row["model_id"],
            "model_version": row["model_version"],
            "robot_id": row["robot_id"],
            "artifact_id": row["artifact_id"],
            "metadata": json.loads(row["metadata_json"]),
            "active": bool(row["active"]),
            "created_at": row["created_at"],
        }

    def captures(self, robot_id: str, *, limit: int = 50) -> tuple[dict[str, Any], ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT c.*, r.disposition, r.rotation_deg, r.distance_mm,
                          r.z_height_mm, r.notes, r.reviewed_at
                   FROM capture c LEFT JOIN review r USING(capture_id)
                   WHERE c.robot_id=? ORDER BY c.created_at DESC LIMIT ?""",
                (robot_id, max(1, int(limit))),
            ).fetchall()
        return tuple(dict(row) for row in rows)
