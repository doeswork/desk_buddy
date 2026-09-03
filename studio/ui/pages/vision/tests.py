"""Provider-selection tests for the split Vision UI package.

Run with:
    QT_QPA_PLATFORM=offscreen python -m studio.ui.pages.vision.tests
"""

from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QTabWidget
from types import SimpleNamespace
from unittest.mock import patch

from ....services.vision.contracts import CONTRACT_SCHEMA
from ....services.vision.manifests import BUILTIN_MANIFESTS
from ....services.vision.access import VisionAccessManager
from ....services.vision.vision_controller.qt_service import _controller_config
from ....services.network import accounts as broker_accounts
from ....services.network import broker_commands
from ....storage.user_config import keys
from ....storage.user_config.settings import Settings
from ..network import NetworkPage
from . import VisionPage
from .state import learned_model_compatible, provider_options


def worker(
    worker_id: str,
    model_id: str,
    kind: str,
    *,
    ready: bool = True,
    version: str = "commit-1",
) -> dict:
    return {
        "schema": CONTRACT_SCHEMA,
        "worker_id": worker_id,
        "worker_kind": "test",
        "host": "test-host",
        "device": "cpu",
        "ready": ready,
        "busy": False,
        "job_kinds": [kind],
        "models": [{"model_id": model_id, "model_version": version, "job_kinds": [kind]}],
        "input_schemas": [],
        "output_schemas": [],
    }


class VisionCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_public_page_import_survives_package_split(self) -> None:
        self.assertEqual(VisionPage.__name__, "VisionPage")
        self.assertEqual(VisionPage.__module__, "studio.ui.pages.vision.page")

    def test_legacy_sources_are_defaults_and_ensemble_has_its_own_route(self) -> None:
        detector, ensemble, depth = BUILTIN_MANIFESTS
        self.assertEqual(detector.model_id, keys.VISION_DETECTOR_MODEL.default)
        self.assertEqual(detector.source, "google/owlv2-base-patch16")
        self.assertEqual(depth.model_id, keys.VISION_DEPTH_MODEL.default)
        self.assertEqual(depth.source, "depth-anything/Depth-Anything-V2-Small-hf")
        self.assertEqual(ensemble.source, "google/owlv2-base-patch16-ensemble")
        self.assertNotEqual(ensemble.model_id, detector.model_id)
        self.assertNotEqual(ensemble.worker_id, detector.worker_id)

    def test_catalog_and_live_status_merge_without_duplicates(self) -> None:
        statuses = [
            worker("zero-shot-hf-1", "owlv2-base", "zero_shot.infer"),
            worker("depth-hf-1", "depth-anything-v2-small", "depth.infer"),
            worker("other-detector", "third-party", "zero_shot.infer"),
        ]
        detectors = provider_options("detector", statuses)
        self.assertEqual(sum(option.key == ("owlv2-base", "zero-shot-hf-1") for option in detectors), 1)
        self.assertTrue(next(option for option in detectors if option.model_id == "owlv2-base").ready)
        self.assertIn(("third-party", "other-detector"), [option.key for option in detectors])
        self.assertNotIn("depth-anything-v2-small", [option.model_id for option in detectors])

    def test_offline_catalog_models_are_visible_and_capability_mismatches_are_filtered(self) -> None:
        wrong_capability = worker("wrong", "owlv2-base", "depth.infer")
        detectors = provider_options("detector", [wrong_capability])
        legacy = next(option for option in detectors if option.model_id == "owlv2-base")
        self.assertFalse(legacy.ready)
        self.assertEqual(legacy.status_text, "Offline")
        self.assertNotIn(("owlv2-base", "wrong"), [option.key for option in detectors])

    def test_provider_compatibility_requires_exact_live_versions(self) -> None:
        detector = provider_options(
            "detector", [worker("zero-shot-hf-1", "owlv2-base", "zero_shot.infer", version="det-a")]
        )[0]
        depth = provider_options(
            "depth", [worker("depth-hf-1", "depth-anything-v2-small", "depth.infer", version="dep-a")]
        )[0]
        record = {
            "metadata": {
                "provider": {
                    "detector_model_id": "owlv2-base",
                    "detector_model_version": "det-a",
                    "depth_model_id": "depth-anything-v2-small",
                    "depth_model_version": "dep-a",
                }
            }
        }
        self.assertTrue(learned_model_compatible(record, detector, depth))
        changed_depth = provider_options(
            "depth", [worker("depth-hf-1", "depth-anything-v2-small", "depth.infer", version="dep-b")]
        )[0]
        self.assertFalse(learned_model_compatible(record, detector, changed_depth))

    def test_capture_actions_require_only_the_selected_detector(self) -> None:
        page = VisionPage()
        config = page.service.config
        page.service.controller = SimpleNamespace(
            config=config,
            detector_model_id=config.detector_model_id,
            detector_worker_id=config.detector_worker_id,
            depth_model_id=config.depth_model_id,
            depth_worker_id=config.depth_worker_id,
        )
        page.state.workers = [
            worker(config.detector_worker_id, config.detector_model_id, "zero_shot.infer")
        ]
        actions = {action.label: action for action in page.build_actions() if hasattr(action, "label")}
        self.assertTrue(actions["Test Photo"].clickable)
        self.assertTrue(actions["Execute"].clickable)
        page.service.controller = None
        page.service.close()

    def test_vision_workspace_has_nested_tabs_and_no_not_built_badge(self) -> None:
        page = VisionPage()
        widget = page.widget()
        workspace = widget.findChild(QTabWidget, "VisionWorkspace")
        self.assertIsNotNone(workspace)
        self.assertEqual([workspace.tabText(i) for i in range(workspace.count())], [
            "Services", "Capture", "Results", "Model Builder",
        ])
        families = widget.findChild(QTabWidget, "VisionServiceFamilies")
        self.assertIsNotNone(families)
        self.assertEqual([families.tabText(i) for i in range(families.count())], [
            "Detection", "Depth", "Custom MLP",
        ])
        self.assertNotIn("NOT BUILT YET", [label.text() for label in widget.findChildren(QLabel)])
        self.assertGreaterEqual(
            len([field for field in widget.findChildren(QLineEdit) if field.objectName() == "ManagedCredential"]),
            10,
        )
        self.assertTrue(any(button.text() == "Copy Worker Setup" for button in widget.findChildren(QPushButton)))
        page.service.close()

    def test_network_is_functional_and_managed_accounts_use_dedicated_actions(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            access = VisionAccessManager(
                os.path.join(temporary, "services"),
                preferences=Settings(QSettings(os.path.join(temporary, "settings.ini"), QSettings.IniFormat)),
                environment={},
            )
            page = NetworkPage(vision_access=access)
            managed = broker_accounts.Account(
                "studio-vision", full_access=True, managed_identity="controller",
            )
            page.selected_account = managed.name
            page._report = SimpleNamespace(installed=True)
            with patch.object(broker_commands, "is_ours", return_value=True), patch.object(
                broker_accounts, "accounts", return_value=[managed],
            ), patch("studio.ui.pages.network.network.broker_card", return_value=QLabel("Broker")):
                actions = {item.label: item for item in page.build_actions() if hasattr(item, "label")}
                widget = page.widget()
            self.assertTrue(page.built)
            self.assertFalse(actions["Reset Password"].clickable)
            self.assertFalse(actions["Remove Account"].clickable)
            self.assertNotIn("NOT BUILT YET", [label.text() for label in widget.findChildren(QLabel)])
            self.assertIsNotNone(widget.findChild(QPushButton, "VisionAccessSetupAll"))


class VisionPreferenceTests(unittest.TestCase):
    def settings(self) -> Settings:
        path = tempfile.mktemp(suffix=".ini")
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        return Settings(QSettings(path, QSettings.IniFormat))

    def test_saved_provider_selection_precedes_builtin_default(self) -> None:
        preferences = self.settings()
        preferences.set(keys.VISION_DETECTOR_MODEL, "saved-detector")
        preferences.set(keys.VISION_DETECTOR_WORKER, "saved-worker")
        preferences.sync()
        config = _controller_config(preferences, {})
        self.assertEqual((config.detector_model_id, config.detector_worker_id), ("saved-detector", "saved-worker"))

    def test_environment_precedes_saved_provider_selection(self) -> None:
        preferences = self.settings()
        preferences.set(keys.VISION_DEPTH_MODEL, "saved-depth")
        preferences.set(keys.VISION_DEPTH_WORKER, "saved-worker")
        preferences.sync()
        config = _controller_config(
            preferences,
            {
                "DESK_BUDDY_DEPTH_MODEL": "environment-depth",
                "DESK_BUDDY_DEPTH_WORKER": "environment-worker",
            },
        )
        self.assertEqual(
            (config.depth_model_id, config.depth_worker_id),
            ("environment-depth", "environment-worker"),
        )


if __name__ == "__main__":
    unittest.main()
