"""Tests for first-class managed Vision services.

Run with:
    python -m studio.services.vision.managed_tests
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import tomllib
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings

from ...storage.user_config.settings import Settings
from ..network import accounts as broker_accounts
from ..network import broker_commands
from .access import VisionAccessIdentity, VisionAccessManager
from .manifests import BUILTIN_MANIFESTS, load_provider_manifest
from .vision_controller.managed_services import CredentialStore, VisionServiceManager
from ..network.broker_finder import find, port_open


def settings_for(root: Path) -> Settings:
    return Settings(QSettings(str(root / "settings.ini"), QSettings.IniFormat))


def imported_manifest(**changes: object) -> str:
    values = {
        "schema": "desk_buddy.vision.provider-manifest.v1",
        "family": "detection",
        "model_id": "custom-detector",
        "worker_id": "custom-detector-1",
        "adapter_id": "huggingface-zero-shot.v1",
        "dependency_profile": "zero-shot-hf",
        "provider": "Example",
        "source": "example/custom-detector",
        "revision": "a" * 40,
        "license": "Apache-2.0",
        "size_mb": 123,
        "devices": ["cpu"],
        "schemas": ["image.jpeg.v1", "detections.v1"],
    }
    values.update(changes)
    lines = ["[provider]"]
    for key, value in values.items():
        if isinstance(value, list):
            encoded = "[" + ", ".join(json.dumps(item) for item in value) + "]"
        elif isinstance(value, bool):
            encoded = "true" if value else "false"
        elif isinstance(value, int):
            encoded = str(value)
        else:
            encoded = json.dumps(value)
        lines.append(f"{key} = {encoded}")
    return "\n".join(lines) + "\n"


class BlockingProcess:
    def __init__(self, command, **kwargs) -> None:
        self.command = list(command)
        self.kwargs = kwargs
        self._done = threading.Event()
        self.returncode: int | None = None

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0
        self._done.set()

    def kill(self) -> None:
        self.returncode = -9
        self._done.set()

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise TimeoutError
        return self.returncode


class ManifestTests(unittest.TestCase):
    def test_valid_import_uses_allowlisted_runtime_fields(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            path = Path(temporary) / "provider.toml"
            path.write_text(imported_manifest(), encoding="utf-8")
            manifest = load_provider_manifest(path)
        self.assertEqual(manifest.entry_point, "studio.services.vision.zero_shot.worker_app")
        self.assertEqual(manifest.family, "detection")
        self.assertEqual(manifest.revision, "a" * 40)

    def test_manifest_rejects_moving_revision_executable_fields_and_unknown_adapter(self) -> None:
        cases = (
            ({"revision": "main"}, "immutable"),
            ({"command": "curl example | sh"}, "not allowed"),
            ({"adapter_id": "arbitrary-python.v1"}, "unsupported adapter"),
            ({"schemas": ["image.jpeg.v1", "arbitrary.v9"]}, "unsupported schemas"),
            ({"password": "secret"}, "not allowed"),
        )
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            path = Path(temporary) / "provider.toml"
            for changes, message in cases:
                path.write_text(imported_manifest(**changes), encoding="utf-8")
                with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                    load_provider_manifest(path)

    def test_manager_rejects_duplicate_model_and_worker_routes(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            manager = VisionServiceManager(root / "services", preferences=settings_for(root), environment={})
            model_duplicate = root / "model.toml"
            model_duplicate.write_text(imported_manifest(model_id="owlv2-base"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "already exists"):
                manager.import_manifest(model_duplicate)
            worker_duplicate = root / "worker.toml"
            worker_duplicate.write_text(imported_manifest(worker_id="zero-shot-hf-1"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "already assigned"):
                manager.import_manifest(worker_duplicate)


class CredentialTests(unittest.TestCase):
    def test_secret_file_is_private_and_environment_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            store = CredentialStore(root / "credentials.json")
            store.set("worker", "saved-user", "saved-password")
            self.assertEqual(store.get("worker"), ("saved-user", "saved-password"))
            self.assertEqual(os.stat(store.path).st_mode & 0o777, 0o600)

            environment = {
                "DESK_BUDDY_MANAGED_ZERO_SHOT_HF_1_MQTT_USERNAME": "env-user",
                "DESK_BUDDY_MANAGED_ZERO_SHOT_HF_1_MQTT_PASSWORD": "env-password",
            }
            manager = VisionServiceManager(
                root / "services", preferences=settings_for(root), environment=environment,
            )
            manager.credentials.set("zero-shot-hf-1", "saved-user", "saved-password")
            self.assertEqual(manager._worker_credentials("zero-shot-hf-1"), ("env-user", "env-password"))

    def test_partial_external_credentials_fail_clearly(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            manager = VisionServiceManager(
                root / "services", preferences=settings_for(root),
                environment={"DESK_BUDDY_STUDIO_MQTT_USERNAME": "missing-password"},
            )
            with self.assertRaisesRegex(RuntimeError, "both Studio MQTT"):
                manager.controller_credentials()


class FakeAccountBackend:
    def __init__(self, *, fail_once_worker: str = "") -> None:
        self.entries: dict[str, broker_accounts.Account] = {}
        self.passwords: dict[str, str] = {}
        self.added: list[str] = []
        self.rotated: list[str] = []
        self.fail_once_worker = fail_once_worker

    def accounts(self) -> list[broker_accounts.Account]:
        return list(self.entries.values())

    def add(self, name: str, **values):
        worker_id = str(values.get("vision_worker_id") or "")
        if worker_id and worker_id == self.fail_once_worker:
            self.fail_once_worker = ""
            return None, "simulated broker failure"
        account = broker_accounts.Account(
            name,
            full_access=bool(values.get("full_access")),
            vision_worker_id=worker_id,
            managed_identity=str(values.get("managed_identity") or ""),
        )
        password = f"password-{len(self.added) + 1}"
        self.entries[name] = account
        self.passwords[name] = password
        self.added.append(name)
        return broker_accounts.NewAccount(account, password), ""

    def mark_managed(self, name: str, identity: str) -> str:
        item = self.entries[name]
        self.entries[name] = broker_accounts.Account(
            item.name, item.full_access, item.vision_worker_id, identity,
        )
        return ""

    def repair_access(self, name: str, **values) -> str:
        self.entries[name] = broker_accounts.Account(
            name,
            full_access=bool(values.get("full_access")),
            vision_worker_id=str(values.get("vision_worker_id") or ""),
            managed_identity=str(values.get("managed_identity") or ""),
        )
        return ""

    def reset_password(self, name: str) -> tuple[str, str]:
        password = f"rotated-{len(self.rotated) + 1}"
        self.passwords[name] = password
        self.rotated.append(name)
        return password, ""

    def remove(self, name: str) -> str:
        self.entries.pop(name, None)
        self.passwords.pop(name, None)
        return ""

    def patches(self) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(patch.object(broker_commands, "is_ours", return_value=True))
        for name in ("accounts", "add", "mark_managed", "repair_access", "reset_password", "remove"):
            stack.enter_context(patch.object(broker_accounts, name, getattr(self, name)))
        return stack


class VisionAccessTests(unittest.TestCase):
    def manager(self, root: Path, environment=None) -> VisionAccessManager:
        return VisionAccessManager(
            root / "services", preferences=settings_for(root), environment=environment or {},
        )

    def test_default_plan_has_all_five_roles_and_stable_existing_account(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            plan = self.manager(Path(temporary)).current_plan()
        self.assertEqual(len(plan.identities), 5)
        self.assertEqual(plan.identity("controller").account_name, "studio-vision")
        self.assertEqual(plan.worker("zero-shot-hf-1").account_name, "vw-1c721538fbe9")
        self.assertEqual(
            {item.role for item in plan.identities},
            {"Studio controller", "Detection", "Depth", "Custom MLP inference", "MLP trainer"},
        )

    def test_known_plan_keeps_an_inactive_managed_route_revokeable(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            manager = self.manager(Path(temporary))
            manager.credentials.set("retired-detector-1", "vw-retired", "saved-password")
            known = manager.known_plan()
        retired = known.worker("retired-detector-1")
        self.assertIsNotNone(retired)
        self.assertEqual(retired.role, "Inactive Vision worker")

    def test_bulk_setup_preserves_existing_secrets_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            manager = self.manager(root)
            plan = manager.current_plan()
            controller = plan.identity("controller")
            detector = plan.worker("zero-shot-hf-1")
            assert controller is not None and detector is not None
            backend = FakeAccountBackend()
            backend.entries[controller.account_name] = broker_accounts.Account(
                controller.account_name, full_access=True,
            )
            backend.entries[detector.account_name] = broker_accounts.Account(
                detector.account_name, vision_worker_id=detector.worker_id,
            )
            manager.credentials.set("controller", controller.account_name, "existing-controller")
            manager.credentials.set(detector.worker_id, detector.account_name, "existing-worker")
            with backend.patches():
                first = manager.ensure_all(plan)
                second = manager.ensure_all(plan)
            self.assertTrue(first.ok and second.ok)
            self.assertEqual(len(backend.added), 3)
            self.assertEqual(manager.credentials.get("controller")[1], "existing-controller")
            self.assertEqual(manager.credentials.get(detector.worker_id)[1], "existing-worker")
            self.assertEqual(backend.entries[controller.account_name].managed_identity, "controller")

    def test_partial_bulk_failure_retries_only_the_missing_identity(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            manager = self.manager(Path(temporary))
            backend = FakeAccountBackend(fail_once_worker="depth-hf-1")
            with backend.patches():
                first = manager.ensure_all()
                first_added = tuple(backend.added)
                second = manager.ensure_all()
            self.assertFalse(first.ok)
            self.assertIn("Depth: simulated broker failure", first.errors)
            self.assertEqual(len(first_added), 4)
            self.assertTrue(second.ok)
            self.assertEqual(len(backend.added), 5)

    def test_owned_acl_mismatch_is_repaired_without_password_rotation(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            manager = self.manager(Path(temporary))
            identity = VisionAccessIdentity("worker:depth-hf-1", "Depth", "depth-hf-1")
            backend = FakeAccountBackend()
            backend.entries[identity.account_name] = broker_accounts.Account(identity.account_name)
            manager.credentials.set(identity.worker_id, identity.account_name, "keep-me")
            with backend.patches():
                state = manager.ensure(identity)
            self.assertTrue(state.ready)
            self.assertEqual(backend.rotated, [])
            self.assertEqual(backend.entries[identity.account_name].vision_worker_id, identity.worker_id)
            self.assertEqual(manager.credentials.get(identity.worker_id)[1], "keep-me")

    def test_rotation_revocation_and_setup_bundle_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            manager = self.manager(Path(temporary))
            identity = VisionAccessIdentity("worker:depth-hf-1", "Depth", "depth-hf-1")
            backend = FakeAccountBackend()
            with backend.patches():
                original = manager.ensure(identity)
                bundle = manager.setup_bundle(identity)
                rotated = manager.rotate(identity)
                revoked = manager.revoke(identity)
            self.assertIn(f"export {identity.environment_prefix}_USERNAME=", bundle)
            self.assertIn('[worker]\nworker_id = "depth-hf-1"', bundle)
            self.assertNotEqual(original.password, rotated.password)
            self.assertEqual(revoked.status, "missing")
            self.assertIsNone(manager.credentials.get(identity.worker_id))

    def test_external_credentials_win_and_external_broker_is_never_modified(self) -> None:
        environment = {
            "DESK_BUDDY_VISION_MQTT_HOST": "mqtt.example.test",
            "DESK_BUDDY_MANAGED_DEPTH_HF_1_MQTT_USERNAME": "external-user",
            "DESK_BUDDY_MANAGED_DEPTH_HF_1_MQTT_PASSWORD": "external-password",
        }
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            manager = self.manager(Path(temporary), environment)
            identity = VisionAccessIdentity("worker:depth-hf-1", "Depth", "depth-hf-1")
            backend = FakeAccountBackend()
            with backend.patches():
                state = manager.ensure(identity)
                with self.assertRaisesRegex(RuntimeError, "cannot be rotated"):
                    manager.rotate(identity)
            self.assertTrue(state.ready)
            self.assertEqual(state.host, "mqtt.example.test")
            self.assertFalse(state.can_manage)
            self.assertEqual(backend.added, [])

    def test_external_broker_without_external_credentials_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            manager = self.manager(
                Path(temporary), {"DESK_BUDDY_VISION_MQTT_HOST": "mqtt.example.test"},
            )
            with self.assertRaisesRegex(RuntimeError, "external MQTT broker"):
                manager.ensure_controller()

    @unittest.skipUnless(
        find("mosquitto") and find("mosquitto_passwd") and find("mosquitto_pub") and find("mosquitto_sub"),
        "Mosquitto broker and client tools are required",
    )
    def test_real_temporary_broker_enforces_all_managed_worker_acls(self) -> None:
        """Exercise the generated ACLs without touching Studio's real broker files."""
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            manager = self.manager(root)
            identities = manager.current_plan().identities
            password_file, acl_file = root / "passwd", root / "acl"
            password = "temporary-test-password"
            account_values = []
            password_tool = find("mosquitto_passwd")
            for index, identity in enumerate(identities):
                command = [password_tool, "-b"]
                if index == 0:
                    command.append("-c")
                command.extend((str(password_file), identity.account_name, password))
                subprocess.run(command, check=True, capture_output=True)
                account_values.append(broker_accounts.Account(
                    identity.account_name,
                    full_access=identity.controller,
                    vision_worker_id=identity.worker_id,
                    managed_identity=identity.identity_id,
                ))
            with patch.object(broker_commands, "acl_path", return_value=acl_file):
                broker_accounts._write_acl(account_values)

            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            config = root / "mosquitto.conf"
            config.write_text(
                "\n".join((
                    f"listener {port} 127.0.0.1",
                    "allow_anonymous false",
                    f"password_file {password_file}",
                    f"acl_file {acl_file}",
                    "persistence false",
                )) + "\n",
                encoding="utf-8",
            )
            process = subprocess.Popen(
                [find("mosquitto"), "-c", str(config)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 3
                while not port_open(port) and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(port_open(port), "temporary broker did not start")
                controller = next(item for item in identities if item.controller)
                for identity in (item for item in identities if item.worker_id):
                    request = f"desk_buddy/vision/worker/{identity.worker_id}/request"
                    subscriber = subprocess.Popen(
                        [
                            find("mosquitto_sub"), "-h", "127.0.0.1", "-p", str(port),
                            "-u", identity.account_name, "-P", password,
                            "-t", request, "-C", "1", "-W", "2",
                        ],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    )
                    time.sleep(0.05)
                    sent = subprocess.run(
                        [
                            find("mosquitto_pub"), "-h", "127.0.0.1", "-p", str(port),
                            "-u", controller.account_name, "-P", password,
                            "-t", request, "-m", identity.worker_id,
                        ],
                        capture_output=True, text=True,
                    )
                    output, _ = subscriber.communicate(timeout=3)
                    self.assertEqual(sent.returncode, 0)
                    self.assertEqual(output.strip(), identity.worker_id)

                    denied_topic = f"firmware/forbidden/{identity.worker_id}"
                    denied = subprocess.run(
                        [
                            find("mosquitto_pub"), "-V", "mqttv5", "-q", "1",
                            "-h", "127.0.0.1", "-p", str(port),
                            "-u", identity.account_name, "-P", password,
                            "-t", denied_topic, "-m", "forbidden", "-r",
                        ],
                        capture_output=True, text=True,
                    )
                    # Mosquitto 2.0 may disconnect an MQTT v5 publisher only
                    # after its CLI has returned success.  Prove denial by
                    # checking that no retained firmware message was stored.
                    received = subprocess.run(
                        [
                            find("mosquitto_sub"), "-h", "127.0.0.1", "-p", str(port),
                            "-u", controller.account_name, "-P", password,
                            "-t", denied_topic, "-C", "1", "-W", "1",
                        ],
                        capture_output=True, text=True,
                    )
                    self.assertEqual(received.stdout, "", identity.role)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


class ManagedLifecycleTests(unittest.TestCase):
    def manager(self, root: Path, **kwargs) -> VisionServiceManager:
        environment = {
            "DESK_BUDDY_MANAGED_ZERO_SHOT_HF_1_MQTT_USERNAME": "worker",
            "DESK_BUDDY_MANAGED_ZERO_SHOT_HF_1_MQTT_PASSWORD": "password",
            "DESK_BUDDY_MANAGED_ZERO_SHOT_OWLV2_ENSEMBLE_1_MQTT_USERNAME": "ensemble",
            "DESK_BUDDY_MANAGED_ZERO_SHOT_OWLV2_ENSEMBLE_1_MQTT_PASSWORD": "password",
        }
        return VisionServiceManager(
            root / "services", repository_root=Path(__file__).parents[3],
            preferences=settings_for(root), environment=environment, **kwargs,
        )

    @staticmethod
    def wait_install(manager: VisionServiceManager) -> None:
        deadline = time.monotonic() + 2
        while manager._install_lock.locked() and time.monotonic() < deadline:
            time.sleep(0.01)
        if manager._install_lock.locked():
            raise AssertionError("installation thread did not finish")

    @staticmethod
    def fake_install_commands(manager: VisionServiceManager, *, fail_download: bool = False) -> None:
        def run(command, family, *, env=None):
            if command[1:3] == ["-m", "venv"]:
                python = Path(command[-1]) / "bin" / "python"
                python.parent.mkdir(parents=True, exist_ok=True)
                python.touch()
            elif any("install_model" in item for item in command):
                if fail_download:
                    raise RuntimeError("download failed")
                cache = Path(env["HF_HOME"])
                (cache / "downloaded.bin").write_bytes(b"weights")
        manager._run_install = run  # type: ignore[method-assign]

    @staticmethod
    def mark_installed(manager: VisionServiceManager, model_id: str) -> None:
        manifest = manager.manifest(model_id, "detection")
        assert manifest is not None
        runtime = manager._runtime_python("detection")
        runtime.parent.mkdir(parents=True, exist_ok=True)
        runtime.touch()
        manager._model_path(manifest).mkdir(parents=True, exist_ok=True)
        manager._write_json(manager._marker_path(manifest), {
            "revision": manifest.revision, "adapter_id": manifest.adapter_id,
        })

    def test_install_is_staged_and_failure_does_not_create_markers(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            manager = self.manager(root)
            self.fake_install_commands(manager)
            manager.install_candidate("detection")
            self.wait_install(manager)
            manifest = BUILTIN_MANIFESTS[0]
            self.assertTrue(manager.state("detection").installed)
            self.assertTrue(manager._marker_path(manifest).exists())
            self.assertFalse(manager._runtime_path("detection").with_name("detection.installing").exists())

        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            manager = self.manager(root)
            self.fake_install_commands(manager, fail_download=True)
            manager.install_candidate("detection")
            self.wait_install(manager)
            self.assertIn("download failed", manager.state("detection").error)
            self.assertFalse(manager._marker_path(BUILTIN_MANIFESTS[0]).exists())
            self.assertFalse(any(path.name.endswith(".installing") for path in manager.model_dir.rglob("*")))

    def test_installation_is_serialized_and_cancel_cleans_staging(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            manager = self.manager(root)
            entered = threading.Event()

            def blocked(command, family, *, env=None):
                if command[1:3] == ["-m", "venv"]:
                    Path(command[-1]).mkdir(parents=True, exist_ok=True)
                    entered.set()
                    while not manager._install_cancel.wait(0.01):
                        pass
                    raise RuntimeError("installation cancelled")

            manager._run_install = blocked  # type: ignore[method-assign]
            manager.install_candidate("detection")
            self.assertTrue(entered.wait(1))
            with self.assertRaisesRegex(RuntimeError, "another vision service installation"):
                manager.install_candidate("depth")
            manager.cancel_install()
            self.wait_install(manager)
            self.assertEqual(manager.state("detection").error, "Installation cancelled")
            self.assertFalse((manager.runtime_dir / "detection.installing").exists())

    def test_generated_config_contains_no_credentials_and_parses(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            manager = self.manager(root)
            manifest = BUILTIN_MANIFESTS[0]
            config = manager._write_worker_config("detection", manifest, local_files_only=True)
            text = config.read_text(encoding="utf-8")
            parsed = tomllib.loads(text)
            self.assertEqual(parsed["models"][manifest.model_id]["revision"], manifest.revision)
            self.assertNotIn("env-password", text)
            self.assertNotIn("env-user", text)
            self.assertEqual(os.stat(config).st_mode & 0o777, 0o600)

    def test_switch_commits_only_exact_mqtt_identity_and_keeps_one_process(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            processes: list[BlockingProcess] = []

            def factory(command, **kwargs):
                process = BlockingProcess(command, **kwargs)
                processes.append(process)
                return process

            manager = self.manager(root, process_factory=factory)
            self.mark_installed(manager, "owlv2-base")
            activated = []
            manager.on_activate = lambda *value: activated.append(value)
            manager.use_and_start("detection")
            manifest = BUILTIN_MANIFESTS[0]
            wrong = {
                "worker_id": manifest.worker_id, "ready": True,
                "job_kinds": [manifest.kind],
                "models": [{"model_id": manifest.model_id, "model_version": "wrong"}],
            }
            manager.sync_mqtt([wrong])
            self.assertEqual(activated, [])
            exact = dict(wrong)
            exact["models"] = [{"model_id": manifest.model_id, "model_version": manifest.revision}]
            manager.sync_mqtt([exact])
            self.assertEqual(activated, [("detection", manifest.model_id, manifest.worker_id)])
            self.assertEqual(len([process for process in processes if process.poll() is None]), 1)
            manager.stop_all()
            self.assertEqual(len([process for process in processes if process.poll() is None]), 0)

    def test_new_route_access_is_provisioned_before_the_running_worker_stops(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            manager = self.manager(root, process_factory=BlockingProcess)
            self.mark_installed(manager, "owlv2-base")
            self.mark_installed(manager, "owlv2-base-ensemble")
            current = BlockingProcess(["current-worker"])
            manager._processes["detection"] = current
            state = manager.state("detection")
            state.active_model_id = "owlv2-base"
            state.active_worker_id = "zero-shot-hf-1"
            state.process_state = "running"
            manager.set_candidate("detection", "owlv2-base-ensemble")
            events = []
            original_stop = manager.stop

            def credentials(worker_id: str):
                events.append(("access", worker_id))
                return "worker", "password"

            def stop(family: str):
                events.append(("stop", family))
                return original_stop(family)

            manager._worker_credentials = credentials  # type: ignore[method-assign]
            manager.stop = stop  # type: ignore[method-assign]
            manager.use_and_start("detection")
            self.assertEqual(events[:2], [
                ("access", "zero-shot-owlv2-ensemble-1"),
                ("stop", "detection"),
            ])
            manager.stop_all()

    def test_switch_timeout_stops_candidate_before_restoring_previous(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            processes: list[BlockingProcess] = []

            def factory(command, **kwargs):
                process = BlockingProcess(command, **kwargs)
                processes.append(process)
                return process

            manager = self.manager(root, process_factory=factory)
            self.mark_installed(manager, "owlv2-base")
            self.mark_installed(manager, "owlv2-base-ensemble")
            manager.use_and_start("detection")
            first = BUILTIN_MANIFESTS[0]
            manager.sync_mqtt([{
                "worker_id": first.worker_id, "ready": True, "job_kinds": [first.kind],
                "models": [{"model_id": first.model_id, "model_version": first.revision}],
            }])
            manager.set_candidate("detection", "owlv2-base-ensemble")
            manager.use_and_start("detection")
            manager.tick(startup_timeout=-1)
            self.assertEqual(manager.state("detection").active_model_id, "owlv2-base")
            self.assertEqual(len([process for process in processes if process.poll() is None]), 1)
            self.assertIn("did not become MQTT-ready", manager.state("detection").error)
            manager.stop_all()

    def test_model_removal_is_scoped_and_preserves_runtime_and_capture_data(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            manager = self.manager(root)
            self.mark_installed(manager, "owlv2-base")
            capture = root / "captures" / "keep.jpg"
            capture.parent.mkdir()
            capture.write_bytes(b"capture")
            runtime = manager._runtime_python("detection")
            manager.remove_candidate_download("detection")
            self.assertTrue(capture.exists())
            self.assertTrue(runtime.exists())
            self.assertFalse(manager.state("detection").installed)

    def test_active_installation_is_independent_of_a_different_candidate(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            manager = self.manager(Path(temporary))
            self.mark_installed(manager, "owlv2-base")
            self.assertTrue(manager.state("detection").active_installed)
            manager.set_candidate("detection", "owlv2-base-ensemble")
            state = manager.state("detection")
            self.assertTrue(state.active_installed)
            self.assertFalse(state.installed)

    def test_start_with_studio_is_persistent(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            preferences = settings_for(root)
            manager = VisionServiceManager(root / "services", preferences=preferences, environment={})
            manager.set_start_with_studio("depth", True)
            restored = VisionServiceManager(root / "services-2", preferences=preferences, environment={})
            self.assertTrue(restored.state("depth").start_with_studio)

    def test_remote_route_is_read_only_state_and_disables_local_autostart(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            manager = self.manager(root)
            manager.set_start_with_studio("detection", True)
            manager.set_external_active("detection", "remote-model", "remote-worker")
            state = manager.state("detection")
            self.assertTrue(state.remote)
            self.assertFalse(state.start_with_studio)
            self.assertEqual((state.active_model_id, state.active_worker_id), ("remote-model", "remote-worker"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
