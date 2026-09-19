"""Atomic, immutable nine-point calibration profiles with active pointers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

PROFILE_SCHEMA = "desk_buddy.vision.calibration-profile.v1"


def _robot_key(robot: str) -> str:
    return hashlib.sha256(robot.encode("utf-8")).hexdigest()[:24]


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


class ProfileStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def create(
        self, *, robot: str, width: int, height: int, points: dict,
        grid: dict, perch: Mapping[str, Any], algorithm: str,
    ) -> dict[str, Any]:
        identity = {
            "schema": PROFILE_SCHEMA, "robot": robot,
            "image_width": int(width), "image_height": int(height),
            "points": points, "grid": grid,
            "capture_pose": {"base_heading_deg": 0.0, "perch": dict(perch)},
            "algorithm": algorithm,
        }
        profile = {
            **identity,
            "profile_id": hashlib.sha256(_canonical(identity)).hexdigest()[:24],
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        return profile

    def save(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(profile)
        robot = str(value.get("robot") or "")
        profile_id = str(value.get("profile_id") or "")
        if value.get("schema") != PROFILE_SCHEMA or not robot or not profile_id:
            raise ValueError("invalid calibration profile")
        directory = self.root / "profiles" / _robot_key(robot)
        profile_path = directory / f"{profile_id}.json"
        if not profile_path.exists():
            self._write_json(profile_path, value)
        self._write_json(self.root / "active" / f"{_robot_key(robot)}.json", {
            "schema": PROFILE_SCHEMA, "robot": robot, "profile_id": profile_id,
            "profile_path": str(profile_path.relative_to(self.root)),
        })
        return value

    def active(self, robot: str) -> dict[str, Any] | None:
        pointer = self._read_json(self.root / "active" / f"{_robot_key(robot)}.json")
        if not pointer or pointer.get("robot") != robot:
            return None
        relative = pointer.get("profile_path")
        if not isinstance(relative, str):
            return None
        profile = self._read_json(self.root / relative)
        if (
            not profile or profile.get("schema") != PROFILE_SCHEMA
            or profile.get("robot") != robot
            or profile.get("profile_id") != pointer.get("profile_id")
        ):
            return None
        return profile

    def invalidate(self, robot: str) -> bool:
        path = self.root / "active" / f"{_robot_key(robot)}.json"
        if not path.exists():
            return False
        path.unlink()
        return True

    def active_ids(self) -> dict[str, str]:
        found: dict[str, str] = {}
        directory = self.root / "active"
        if not directory.is_dir():
            return found
        for path in directory.glob("*.json"):
            value = self._read_json(path)
            if value and isinstance(value.get("robot"), str) and isinstance(value.get("profile_id"), str):
                found[value["robot"]] = value["profile_id"]
        return dict(sorted(found.items()))

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _write_json(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
