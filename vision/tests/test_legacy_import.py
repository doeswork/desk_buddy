from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from tests.support import ROOT

from desk_buddy_vision.legacy_import import LegacyImporter
from desk_buddy_vision.storage import VisionStorage


class LegacyImportTests(unittest.TestCase):
    def test_reviewed_grab_attempt_becomes_explicit_feature_and_correction_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "legacy.sqlite3"
            connection = sqlite3.connect(legacy)
            connection.execute(
                """
                CREATE TABLE grab_attempts (
                    id INTEGER PRIMARY KEY,
                    attempt_id TEXT,
                    created_at TEXT,
                    success INTEGER,
                    notes TEXT,
                    ml_notes TEXT,
                    topic TEXT,
                    phrase TEXT,
                    label TEXT,
                    confidence REAL,
                    bbox_x0 REAL,
                    bbox_y0 REAL,
                    bbox_x1 REAL,
                    bbox_y1 REAL,
                    image_width INTEGER,
                    image_height INTEGER,
                    depth_patch BLOB,
                    reviewed_for_ml INTEGER,
                    target_y_offset_mm REAL,
                    target_rotation_adjust_deg REAL,
                    commanded_rotation_value REAL,
                    commanded_rotation_direction TEXT,
                    commanded_ik_distance_mm REAL,
                    commanded_ik_z_height_mm REAL,
                    distance_mm REAL,
                    z_height_mm REAL
                )
                """
            )
            patch = np.linspace(0, 1, 4096, dtype=np.float16).tobytes()
            connection.execute(
                """
                INSERT INTO grab_attempts VALUES (
                    1, 'attempt-1', '2026-01-01T00:00:00Z', 1, '', 'reviewed',
                    'legacy/robot/test', 'blue block', 'blue block', 0.9,
                    10, 20, 110, 120, 200, 200, ?, 1,
                    4.5, -2.0, 10, 'LEFT', 80, 5, 80, 7
                )
                """,
                (patch,),
            )
            connection.commit()
            connection.close()

            destination = root / "new-data"
            importer = LegacyImporter(
                data_dir=destination,
                topic_map={"legacy/robot/test": "robot-1"},
            )
            try:
                importer.import_grab_attempts(legacy)
                self.assertEqual(importer.stats.attempts_imported, 1)
            finally:
                importer.close()

            storage = VisionStorage(destination / "vision.sqlite3")
            examples = storage.training_examples(robot_id="robot-1")
            self.assertEqual(len(examples), 1)
            self.assertEqual(examples[0]["target_rotation_delta_deg"], -2.0)
            self.assertEqual(examples[0]["target_distance_delta_mm"], 4.5)
            self.assertEqual(examples[0]["target_z_delta_mm"], 2.0)
            artifact = storage.get_artifact(examples[0]["feature_snapshot_artifact_id"])
            self.assertEqual(artifact.metadata["feature_schema_version"], "features.v1")
            storage.close()


if __name__ == "__main__":
    unittest.main()

