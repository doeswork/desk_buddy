"""Zero-shot vision contracts, service, manager, and MQTT bridge tests.

    QT_QPA_PLATFORM=offscreen python -m studio.services.vision.tests

All inference and downloads are faked. Ordinary CI must never fetch weights.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QProcess, QSettings, QTimer
from PySide6.QtWidgets import QApplication

from ...services.network.pub_sub.client import MqttClient
from ...storage import keys
from ...storage.settings import Settings
from . import access
from .access import PASSWORD_ENV, USERNAME_ENV, VisionCredentials
from . import bootstrap
from .bootstrap import RUNTIME_REQUIREMENTS, build_worker_zipapp
from .catalog import BUILTIN_MODELS, DEFAULT_MODEL_ID, ModelManifest, validate_catalog
from .contracts import normalize_batch
from .frames import decode_frame, encode_frame
from .manager import RESULT_TOPIC, STATUS_SCHEMA, VisionServiceManager
from .worker_runtime.desk_buddy_vision_worker.detector import HuggingFaceDetector, formatted_prompt
from .worker_runtime.desk_buddy_vision_worker.catalog import CURATED_MODELS, validate_model
from .worker_runtime.desk_buddy_vision_worker.service import DetectorService

_app = QApplication.instance() or QApplication([])
JPEG = b"\xff\xd8jpeg-data-with-a-}-byte\xff\xd9"


def raises(kind, call) -> Exception:
    try:
        call()
    except kind as exc:
        return exc
    raise AssertionError(f"expected {kind.__name__}")


class FakeClient:
    def __init__(self, *, running: bool = True) -> None:
        self.running = running
        self.status = "Connected" if running else "Broker is not running"
        self.json_subscribers: dict[str, list] = {}
        self.raw_subscribers: dict[str, list] = {}
        self.json_sent: list[tuple[str, dict, int]] = []
        self.raw_sent: list[tuple[str, bytes, int]] = []

    def reconcile(self) -> None:
        pass

    def subscribe(self, topic, callback):
        self.json_subscribers.setdefault(topic, []).append(callback)
        return lambda: self.json_subscribers.get(topic, []).remove(callback)

    def subscribe_raw(self, topic, callback):
        self.raw_subscribers.setdefault(topic, []).append(callback)
        return lambda: self.raw_subscribers.get(topic, []).remove(callback)

    def publish(self, topic, payload, *, qos=0):
        body = dict(payload)
        self.json_sent.append((topic, body, qos))
        return str(body.get("action_id") or "")

    def publish_raw(self, topic, payload, *, qos=1):
        if not self.running:
            return False
        self.raw_sent.append((topic, bytes(payload), qos))
        return True


@contextmanager
def temporary_manager(*, client: FakeClient | None = None):
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name)
    preferences = Settings(QSettings(str(root / "settings.ini"), QSettings.IniFormat))
    fake = client or FakeClient()
    manager = VisionServiceManager(root, preferences=preferences, client=fake, environment={})
    try:
        yield manager, fake
    finally:
        manager.shutdown()
        directory.cleanup()


def mark_installed(manager: VisionServiceManager, model_id: str) -> None:
    manifest = next(item for item in BUILTIN_MODELS if item.model_id == model_id)
    runtime = manager._runtime_python()
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_bytes(b"python")
    manager._model_path(manifest).mkdir(parents=True, exist_ok=True)
    manager._write_json(manager._marker_path(manifest), {
        "model_id": manifest.model_id,
        "revision": manifest.revision,
    })


def ready_status(manager: VisionServiceManager, **changes) -> dict:
    manifest = next(item for item in BUILTIN_MODELS if item.model_id == manager._expected_model_id)
    value = {
        "schema": STATUS_SCHEMA,
        "service_id": "detector",
        "launch_id": manager._expected_launch_id,
        "model_id": manifest.source,
        "revision": manifest.revision,
        "state": "ready",
        "ready": True,
        "busy": False,
        "device": "cpu",
    }
    value.update(changes)
    return value


def test_catalog_is_closed_pinned_and_defaults_to_owlv2() -> None:
    assert DEFAULT_MODEL_ID == "owlv2-base"
    assert [item.source for item in BUILTIN_MODELS] == [
        "google/owlv2-base-patch16",
        "IDEA-Research/grounding-dino-tiny",
    ]
    assert all(len(item.revision) == 40 for item in BUILTIN_MODELS)
    assert all(item.license == "Apache-2.0" for item in BUILTIN_MODELS)
    for item in BUILTIN_MODELS:
        assert CURATED_MODELS[item.model_id]["source"] == item.source
        validate_model({**item.as_dict(), "cache_dir": "/model"})
    raises(ValueError, lambda: ModelManifest(
        "moving", "Moving", "Unknown", "owner/model", "main", 1,
        "Apache-2.0", "nested", 0.5,
    ))
    raises(ValueError, lambda: validate_model({
        **BUILTIN_MODELS[0].as_dict(), "cache_dir": "/model", "revision": "a" * 40,
    }))
    raises(ValueError, lambda: validate_model({
        **BUILTIN_MODELS[0].as_dict(), "cache_dir": "/model", "trust_remote_code": True,
    }))
    raises(ValueError, lambda: validate_catalog((BUILTIN_MODELS[0], BUILTIN_MODELS[0])))
    raises(ValueError, lambda: validate_catalog(BUILTIN_MODELS, "not-in-catalog"))


def test_prompts_and_thresholds_are_manifest_controlled() -> None:
    owl, dino = BUILTIN_MODELS
    assert formatted_prompt(" red   Mug ", owl.prompt_style) == [["red Mug"]]
    assert formatted_prompt(" Red MUG. ", dino.prompt_style) == ["red mug."]
    assert owl.box_threshold == 0.25 and owl.text_threshold is None
    assert dino.box_threshold == 0.4 and dino.text_threshold == 0.3
    raises(ValueError, lambda: formatted_prompt("  ", "nested"))


def test_worker_runtime_pins_scipy_and_matches_the_requirements_asset() -> None:
    assert "scipy==1.18.1" in RUNTIME_REQUIREMENTS
    requirements = (
        Path(__file__).with_name("worker-requirements.txt")
        .read_text(encoding="utf-8").splitlines()
    )
    assert requirements == list(RUNTIME_REQUIREMENTS)


def test_processor_dependencies_are_exercised_before_ready() -> None:
    calls = []

    class Processor:
        def __call__(self, **values):
            calls.append(values)

    detector = HuggingFaceDetector.__new__(HuggingFaceDetector)
    detector.processor = Processor()
    detector.prompt_style = "nested"
    detector._verify_processor()
    assert calls[0]["text"] == [["object"]]
    assert calls[0]["return_tensors"] == "pt"


def test_detection_contract_clamps_drops_and_sorts_boxes() -> None:
    batch = normalize_batch(
        width=100, height=50, prompt="mug", model_id="model", revision="a" * 40,
        detections=(
            {"label": "low", "score": 0.2, "box": [-3, 2, 20, 60]},
            {"label": "right", "score": 0.9, "box": [40, 10, 80, 40]},
            {"label": "left", "score": 0.9, "box": [10, 10, 20, 20]},
            {"label": "reversed", "score": 1, "box": [20, 20, 10, 10]},
            {"label": "bad score", "score": 2, "box": [1, 1, 2, 2]},
        ),
    )
    assert batch["schema"] == "detections.v1"
    assert [item["label"] for item in batch["detections"]] == ["left", "right", "low"]
    low = batch["detections"][-1]
    assert low["box_px"] == [0.0, 2.0, 20.0, 50]
    assert low["center_normalized"] == [0.1, 0.52]
    assert low["model_id"] == "model"


def test_empty_batch_is_an_explicit_empty_detection_list() -> None:
    batch = normalize_batch(
        width=1, height=1, prompt="thing", model_id="m", revision="r",
        detections=(),
    )
    assert batch["detections"] == []


def test_binary_frame_keeps_braces_inside_jpeg_and_rejects_damage() -> None:
    payload = encode_frame({"action_id": "photo-1", "payload": "ignored"}, JPEG)
    frame = decode_frame(payload)
    assert frame.metadata == {"action_id": "photo-1"}
    assert frame.jpeg == JPEG
    raises(ValueError, lambda: decode_frame(b'{"action_id":"x"}' + JPEG))
    raises(ValueError, lambda: encode_frame({}, b"not-a-jpeg"))


def test_raw_and_json_subscribers_can_share_one_topic() -> None:
    client = MqttClient()
    raw, decoded = [], []
    stop_raw = client.subscribe_raw("robot/test", lambda topic, body: raw.append((topic, body)))
    stop_json = client.subscribe("robot/test", lambda topic, body: decoded.append((topic, body)))
    message = SimpleNamespace(topic="robot/test", payload=b'{"status":"completed"}')
    client._on_message(None, None, message)
    binary = SimpleNamespace(topic="robot/test", payload=encode_frame({"action_id": "x"}, JPEG))
    client._on_message(None, None, binary)
    assert raw == [("robot/test", message.payload), ("robot/test", binary.payload)]
    assert decoded == [("robot/test", {"status": "completed"})]
    stop_json()
    stop_raw()
    assert "robot/test" not in client._subscribers
    assert "robot/test" not in client._raw_subscribers


def test_manager_requires_exact_launch_model_and_revision_for_ready() -> None:
    with temporary_manager() as (manager, _client):
        manager._expected_launch_id = "launch-1"
        manager._expected_model_id = DEFAULT_MODEL_ID
        manager._replace(operation="start", process_state="starting", mqtt_state="waiting")
        manager._on_status(ready_status(manager, launch_id="stale"))
        manager._on_status(ready_status(manager, revision="b" * 40))
        assert not manager.state.ready
        manager._on_status(ready_status(manager))
        assert manager.state.ready
        assert manager.state.active_model_id == DEFAULT_MODEL_ID
        assert manager.preferences.get(keys.VISION_ACTIVE_MODEL) == DEFAULT_MODEL_ID


def test_manager_defaults_selection_and_persists_separate_active_setting() -> None:
    with temporary_manager() as (manager, _client):
        assert manager.selected.model_id == DEFAULT_MODEL_ID
        manager.select_model("grounding-dino-tiny")
        assert manager.preferences.get(keys.VISION_SELECTED_MODEL) == "grounding-dino-tiny"
        assert manager.preferences.get(keys.VISION_ACTIVE_MODEL) == ""
        assert not manager.state.start_with_studio


def test_stopping_during_start_clears_the_lifecycle_operation() -> None:
    with temporary_manager() as (manager, _client):
        manager._switch_previous = ("grounding-dino-tiny", True)
        manager._is_rollback = True
        manager._rollback_error = "candidate failed"
        manager._replace(operation="rollback", process_state="starting", phase="startup")
        manager.stop()
        assert manager.state.operation == ""
        assert manager.state.process_state == "stopped"
        assert manager.state.phase == ""
        assert manager._switch_previous is None
        assert not manager._is_rollback


def test_stopping_old_detector_during_download_does_not_cancel_install_or_rollback() -> None:
    with temporary_manager() as (manager, _client):
        manager._switch_previous = (DEFAULT_MODEL_ID, True)
        manager._replace(operation="install", process_state="running", phase="model")
        manager.stop()
        assert manager.state.operation == "install"
        assert manager.state.phase == "model"
        assert manager._switch_previous == (DEFAULT_MODEL_ID, False)


def test_local_preview_sends_bytes_and_correlates_terminal_result() -> None:
    with temporary_manager() as (manager, client):
        manager._replace(
            active_model_id=DEFAULT_MODEL_ID, process_state="running", mqtt_state="ready",
        )
        action_id = manager.detect_local(JPEG, "red mug")
        assert action_id
        topic, payload, qos = client.raw_sent[-1]
        request = decode_frame(payload)
        assert topic == "vision/detector/request" and qos == 1
        assert request.metadata["prompt"] == "red mug"
        assert request.metadata["model_name"] == BUILTIN_MODELS[0].source
        assert request.metadata["use_model"] is False
        assert request.jpeg == JPEG
        manager._on_result(RESULT_TOPIC, {
            "sender": "visual_ai", "action_id": action_id,
            "status": "completed", "detection_batch": {"detections": []},
        })
        assert manager.state.pending_detection == ""


def test_robot_preview_is_always_non_motion_and_requires_live_mqtt() -> None:
    with temporary_manager() as (manager, client):
        manager._replace(
            active_model_id=DEFAULT_MODEL_ID, process_state="running", mqtt_state="ready",
        )
        action_id = manager.detect_robot("black", "mug")
        topic, body, qos = client.json_sent[-1]
        assert action_id and topic == "black/test" and qos == 1
        assert body["use_model"] is False
        assert body["model_name"] == BUILTIN_MODELS[0].source

    with temporary_manager(client=FakeClient(running=False)) as (manager, _client):
        manager._replace(
            active_model_id=DEFAULT_MODEL_ID, process_state="running", mqtt_state="ready",
        )
        assert manager.detect_robot("black", "mug") == ""
        assert "Broker is not running" in manager.state.error


def test_paho_bridge_reaches_manager_on_the_gui_thread() -> None:
    with temporary_manager() as (manager, _client):
        manager._expected_launch_id = "thread-launch"
        manager._expected_model_id = DEFAULT_MODEL_ID
        manager._replace(operation="start", process_state="starting", mqtt_state="waiting")
        handled = {}

        def changed(state) -> None:
            if state.ready:
                handled["thread"] = threading.current_thread().name
                _app.quit()

        manager.changed.connect(changed)
        threading.Thread(
            target=lambda: manager._bridge.status_received.emit(ready_status(manager)),
            name="paho-network", daemon=True,
        ).start()
        QTimer.singleShot(3000, _app.quit)
        _app.exec()
        assert handled.get("thread") == threading.main_thread().name, handled


def test_install_state_preserves_a_running_detector_during_download() -> None:
    class RunningProcess:
        def state(self):
            return QProcess.Running

    with temporary_manager() as (manager, _client):
        manager._worker_process = RunningProcess()
        manager._replace(
            active_model_id="grounding-dino-tiny",
            process_state="running", mqtt_state="ready",
        )
        manager._runtime_python().parent.mkdir(parents=True)
        manager._runtime_python().write_bytes(b"python")
        with mock.patch("studio.services.vision.manager.materialize_bootstrap", return_value=(Path("uv"), Path("worker"))), \
                mock.patch.object(manager, "_download_model"):
            manager.install_and_use()
        assert manager.state.operation == "install"
        assert manager.state.ready
        manager._worker_process = None
        manager.cancel_install()


def test_runtime_upgrade_reuses_an_existing_model_snapshot() -> None:
    with temporary_manager() as (manager, _client):
        mark_installed(manager, DEFAULT_MODEL_ID)
        manifest = manager.selected
        manager._runtime_python().unlink()
        staging = manager.root / "models" / ".runtime-upgrade.installing"
        staging.mkdir(parents=True)
        manager._worker = Path("worker.pyz")
        manager._model_staging = staging
        manager._replace(operation="install", phase="model")
        with mock.patch.object(manager, "_provision_model") as provision:
            manager._download_model()
        provision.assert_called_once_with()
        assert manager._model_staging is None
        assert manager._snapshot_installed(manifest)
        assert "Reusing" in manager.state.progress_message


def test_config_and_logs_never_persist_the_mqtt_password() -> None:
    with temporary_manager() as (manager, _client):
        secret = "do-not-write-this"
        credentials = VisionCredentials("localhost", 1883, "vision", secret, ("vision/#",), "managed")
        config = manager._write_config(
            manager.selected, cache_dir=manager.root / "models", credentials=credentials,
            launch_id="secret-test",
        )
        assert secret not in config.read_text(encoding="utf-8")
        manager._credentials = credentials
        manager._append_log(f"dependency printed {secret}\n".encode())
        assert secret not in (manager.root / "logs" / "detector.log").read_text(encoding="utf-8")
        assert "[redacted]" in (manager.root / "logs" / "detector.log").read_text(encoding="utf-8")


def test_mqtt_secret_is_only_passed_to_the_serving_worker() -> None:
    client = FakeClient()
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name)
    preferences = Settings(QSettings(str(root / "settings.ini"), QSettings.IniFormat))
    manager = VisionServiceManager(
        root, preferences=preferences, client=client,
        environment={USERNAME_ENV: "external", PASSWORD_ENV: "external-secret", "SAFE": "yes"},
    )
    try:
        manager._credentials = VisionCredentials(
            "broker", 1883, "external", "external-secret", ("vision/#",), "environment",
        )
        installer = manager._process_environment(include_credentials=False)
        worker = manager._process_environment(include_credentials=True)
        assert installer.value("SAFE") == "yes"
        assert not installer.contains(PASSWORD_ENV) and not installer.contains(USERNAME_ENV)
        assert worker.value(USERNAME_ENV) == "external"
        assert worker.value(PASSWORD_ENV) == "external-secret"
    finally:
        manager.shutdown()
        directory.cleanup()


def test_access_uses_environment_pair_or_preserves_an_unknown_account() -> None:
    with mock.patch.object(access, "studio_endpoint", return_value=("broker", 1883)):
        value = access.ensure_access({USERNAME_ENV: "external-vision", PASSWORD_ENV: "pw"})
    assert value.username == "external-vision" and value.source == "environment"

    unknown = SimpleNamespace(name="vision", password="", topics=("#",))
    with mock.patch.object(access, "studio_endpoint", return_value=("127.0.0.1", 1883)), \
            mock.patch.object(access.system, "accounts", return_value=[unknown]), \
            mock.patch.object(access.system, "set_topics") as set_topics:
        error = raises(RuntimeError, lambda: access.ensure_access({}))
    assert "does not know its password" in str(error)
    set_topics.assert_not_called()


def test_external_broker_requires_explicit_vision_credentials() -> None:
    with mock.patch.object(access, "studio_endpoint", return_value=("remote.example", 1883)), \
            mock.patch.object(access, "lan_address", return_value="192.168.1.8"), \
            mock.patch.object(access.system, "accounts") as accounts:
        error = raises(RuntimeError, lambda: access.ensure_access({}))
    assert USERNAME_ENV in str(error) and PASSWORD_ENV in str(error)
    assert "vision/#" in str(error)
    accounts.assert_not_called()


def test_known_vision_account_is_restricted_to_service_and_robot_topics() -> None:
    registry = SimpleNamespace(all=lambda: [SimpleNamespace(name="black"), SimpleNamespace(name="silver")])
    known = SimpleNamespace(name="vision", password="recorded", topics=("#",))
    change = SimpleNamespace(problem="")
    with mock.patch.object(access, "studio_endpoint", return_value=("127.0.0.1", 1883)), \
            mock.patch.object(access, "robots", return_value=registry), \
            mock.patch.object(access.system, "accounts", return_value=[known]), \
            mock.patch.object(access.system, "set_topics", return_value=change) as set_topics:
        value = access.ensure_access({})
    expected = ("vision/#", "black/test", "silver/test")
    set_topics.assert_called_once_with("vision", expected)
    assert value.topics == expected and value.password == "recorded"


def test_vision_acl_with_isolated_mosquitto() -> None:
    """Exercise both halves of the exact read/write ACL when tools exist."""
    broker = shutil.which("mosquitto")
    password_tool = shutil.which("mosquitto_passwd")
    if not broker or not password_tool:
        return

    import paho.mqtt.client as mqtt

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        password_file = root / "passwd"
        acl_file = root / "acl"
        config_file = root / "mosquitto.conf"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        first = subprocess.run(
            [password_tool, "-c", "-b", str(password_file), "vision", "vision-secret"],
            capture_output=True, text=True,
        )
        second = subprocess.run(
            [password_tool, "-b", str(password_file), "observer", "observer-secret"],
            capture_output=True, text=True,
        )
        assert first.returncode == second.returncode == 0, first.stderr or second.stderr
        acl_file.write_text(
            "user vision\n"
            "topic readwrite vision/#\n"
            "topic readwrite black/test\n\n"
            "user observer\n"
            "topic readwrite #\n",
            encoding="utf-8",
        )
        config_file.write_text(
            f"listener {port} 127.0.0.1\n"
            "allow_anonymous false\n"
            f"password_file {password_file}\n"
            f"acl_file {acl_file}\n"
            "persistence false\n",
            encoding="utf-8",
        )
        process = subprocess.Popen(
            [broker, "-c", str(config_file)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        clients = []
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError(process.stdout.read())
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                raise AssertionError("isolated Mosquitto did not start")

            def connected(name: str, password: str):
                ready = threading.Event()
                outcome = []
                client = mqtt.Client(
                    mqtt.CallbackAPIVersion.VERSION2,
                    client_id=f"vision-test-{name}-{uuid.uuid4().hex[:8]}",
                    protocol=mqtt.MQTTv311,
                )
                client.username_pw_set(name, password)
                client.on_connect = lambda _c, _u, _f, reason, _p: (
                    outcome.append(getattr(reason, "value", reason)), ready.set()
                )
                client.connect("127.0.0.1", port, keepalive=10)
                client.loop_start()
                clients.append(client)
                assert ready.wait(3) and outcome == [0]
                return client

            observer = connected("observer", "observer-secret")
            vision = connected("vision", "vision-secret")
            received = []
            delivered = threading.Event()
            observer.on_message = lambda _c, _u, message: (
                received.append((message.topic, bytes(message.payload))), delivered.set()
            )
            observer.subscribe("#", qos=1)

            vision_received = []
            vision_delivered = threading.Event()
            vision.on_message = lambda _c, _u, message: (
                vision_received.append((message.topic, bytes(message.payload))),
                vision_delivered.set(),
            )

            subscribed = threading.Event()
            codes = []
            vision.on_subscribe = lambda _c, _u, _mid, reasons, _p: (
                codes.extend(getattr(reason, "value", reason) for reason in reasons),
                subscribed.set(),
            )
            # MQTT 3.1.1 grants the filter at the requested QoS and enforces
            # Mosquitto's read ACL when matching individual messages.
            vision.subscribe("#", qos=1)
            assert subscribed.wait(3) and codes == [1], codes

            observer.publish("unrelated/read-check", b"not-visible", qos=1).wait_for_publish(3)
            assert not vision_delivered.wait(0.3)
            observer.publish("vision/read-check", b"visible", qos=1).wait_for_publish(3)
            assert vision_delivered.wait(3)
            assert vision_received[-1] == ("vision/read-check", b"visible")

            received.clear()
            delivered.clear()
            vision.publish("vision/check", b"allowed", qos=1).wait_for_publish(3)
            assert delivered.wait(3) and received[-1] == ("vision/check", b"allowed")
            delivered.clear()
            vision.publish("black/test", b"robot-allowed", qos=1).wait_for_publish(3)
            assert delivered.wait(3) and received[-1] == ("black/test", b"robot-allowed")
            delivered.clear()
            vision.publish("unrelated/check", b"denied", qos=1).wait_for_publish(3)
            assert not delivered.wait(0.3)
            assert all(topic != "unrelated/check" for topic, _body in received)
        finally:
            for client in clients:
                client.disconnect()
                client.loop_stop()
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


class PublishingClient:
    def __init__(self) -> None:
        self.sent = []

    def publish(self, topic, payload, **options) -> None:
        self.sent.append((topic, json.loads(payload), options))

    def subscribe(self, topic, **options) -> None:
        self.sent.append(("subscribed", topic, options))


class FakeDetector:
    catalog_id = DEFAULT_MODEL_ID
    source = BUILTIN_MODELS[0].source
    model_id = source
    revision = BUILTIN_MODELS[0].revision
    device = "cpu"

    def detect(self, jpeg, prompt):
        assert jpeg == JPEG
        return normalize_batch(
            width=100, height=50, prompt=prompt, model_id=self.model_id,
            revision=self.revision,
            detections=({"label": prompt, "score": 0.8, "box": [10, 10, 50, 40]},),
        )


def service() -> tuple[DetectorService, PublishingClient]:
    manifest = BUILTIN_MODELS[0]
    instance = DetectorService({
        "launch_id": "test-launch",
        "mqtt": {"username_env": USERNAME_ENV, "password_env": PASSWORD_ENV},
        "model": {**manifest.as_dict(), "cache_dir": "/unused"},
    })
    instance.detector = FakeDetector()
    client = PublishingClient()
    instance.client = client
    return instance, client


def run_one_job(instance: DetectorService) -> None:
    thread = threading.Thread(target=instance._work_loop, daemon=True)
    thread.start()
    instance.jobs.put(None, timeout=2)
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_fake_worker_completes_local_preview_and_selects_best_box() -> None:
    instance, client = service()
    payload = encode_frame({
        "schema": "desk_buddy.vision.detect-request.v1",
        "action_id": "local-1", "prompt": "mug", "model_name": DEFAULT_MODEL_ID,
    }, JPEG)
    instance._receive_local(payload)
    run_one_job(instance)
    results = [body for topic, body, _options in client.sent if topic == RESULT_TOPIC]
    assert results[-1]["status"] == "completed"
    assert results[-1]["stage"] == "detection_only"
    assert results[-1]["selected_detection"]["score"] == 0.8
    assert results[-1]["model_id"] == BUILTIN_MODELS[0].source


def test_fake_worker_republishes_exact_ready_status_after_reconnect() -> None:
    instance, client = service()
    instance.loaded = True
    instance.robot_topics = ("black/test",)
    instance._on_connect(client, None, None, 0, None)
    status = [body for topic, body, _options in client.sent if topic == "vision/detector/status"][-1]
    assert status["state"] == "ready"
    assert status["model_id"] == BUILTIN_MODELS[0].source
    assert status["revision"] == BUILTIN_MODELS[0].revision


def test_fake_worker_rejects_motion_model_mismatch_and_reused_ids() -> None:
    instance, client = service()
    framed = encode_frame({
        "schema": "desk_buddy.vision.detect-request.v1", "action_id": "move",
        "prompt": "mug", "use_model": True,
    }, JPEG)
    instance._receive_local(framed)
    mismatch = encode_frame({
        "schema": "desk_buddy.vision.detect-request.v1", "action_id": "wrong-model",
        "prompt": "mug", "model_name": "not-active",
    }, JPEG)
    instance._receive_local(mismatch)

    message = lambda body: SimpleNamespace(topic="black/test", payload=json.dumps(body).encode())
    request = {"action": "detect_object", "action_id": "same", "phrase": "mug", "sender": "studio"}
    instance._on_message(None, None, message(request))
    instance._on_message(None, None, message(request))
    errors = [body["error"]["code"] for _topic, body, _options in client.sent if body.get("status") == "failed"]
    assert errors == [
        "automatic_execution_not_available", "model_mismatch", "duplicate_action_id",
    ]
    assert instance.requests == {}


def test_robot_photo_action_id_mismatch_is_terminal() -> None:
    instance, client = service()
    request = SimpleNamespace(topic="black/test", payload=json.dumps({
        "action": "detect_object", "action_id": "expected", "phrase": "mug", "sender": "studio",
    }).encode())
    instance._on_message(None, None, request)
    instance._on_message(None, None, SimpleNamespace(
        topic="black/test", payload=encode_frame({"action_id": "different"}, JPEG),
    ))
    result = [body for topic, body, _options in client.sent if topic == "black/test"][-1]
    assert result["action_id"] == "expected"
    assert result["error"]["code"] == "action_id_mismatch"


def test_malformed_correlated_images_are_terminal_failures() -> None:
    instance, client = service()
    malformed_local = encode_frame({
        "schema": "desk_buddy.vision.detect-request.v1",
        "action_id": "broken-local", "prompt": "mug",
    }, JPEG).replace(b"\xff\xd9}", b"bad}")
    instance._receive_local(malformed_local)

    request = SimpleNamespace(topic="black/test", payload=json.dumps({
        "action": "detect_object", "action_id": "broken-robot",
        "phrase": "mug", "sender": "studio",
    }).encode())
    instance._on_message(None, None, request)
    instance._on_message(None, None, SimpleNamespace(
        topic="black/test", payload=b'{"action_id":"broken-robot","payload":not-jpeg}',
    ))

    failures = [
        body for _topic, body, _options in client.sent
        if body.get("status") == "failed"
    ]
    assert [body["action_id"] for body in failures] == ["broken-local", "broken-robot"]
    assert all(body["error"]["code"] == "invalid_image" for body in failures)


def test_fake_firmware_robot_preview_returns_correlated_boxes() -> None:
    instance, client = service()
    request = SimpleNamespace(topic="black/test", payload=json.dumps({
        "action": "detect_object", "action_id": "robot-1", "phrase": "red mug",
        "sender": "studio", "use_model": False, "model_name": DEFAULT_MODEL_ID,
    }).encode())
    instance._on_message(None, None, request)
    instance._on_message(None, None, SimpleNamespace(
        topic="black/test", payload=encode_frame({
            "sender": "firmware", "action_id": "robot-1", "photo": "sending_photo",
        }, JPEG),
    ))
    run_one_job(instance)
    result = [body for topic, body, _options in client.sent if topic == "black/test"][-1]
    assert result["action_id"] == "robot-1" and result["status"] == "completed"
    assert result["detection_batch"]["detections"][0]["label"] == "red mug"


def test_no_detection_is_a_terminal_preview_failure() -> None:
    instance, client = service()

    def none(_jpeg, prompt):
        return normalize_batch(
            width=10, height=10, prompt=prompt, model_id=instance.detector.model_id,
            revision=instance.detector.revision, detections=(),
        )

    instance.detector.detect = none
    instance._receive_local(encode_frame({
        "schema": "desk_buddy.vision.detect-request.v1", "action_id": "none",
        "prompt": "missing object", "model_name": DEFAULT_MODEL_ID,
    }, JPEG))
    run_one_job(instance)
    result = [body for topic, body, _options in client.sent if topic == RESULT_TOPIC][-1]
    assert result["status"] == "failed"
    assert result["error"]["code"] == "no_detection"
    assert result["detection_batch"]["detections"] == []


def test_worker_zipapp_builds_and_self_tests_without_runtime_dependencies() -> None:
    with tempfile.TemporaryDirectory() as directory:
        worker = build_worker_zipapp(Path(directory) / "vision-worker.pyz")
        with zipfile.ZipFile(worker) as archive:
            assert not any("__pycache__" in name or name.endswith(".pyc") for name in archive.namelist())
        result = subprocess.run(
            [sys.executable, str(worker), "self-test"], capture_output=True, text=True,
            timeout=20,
        )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True


def test_worker_zipapp_rejects_a_non_curated_install_before_importing_huggingface() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        worker = build_worker_zipapp(root / "vision-worker.pyz")
        config = root / "config.json"
        config.write_text(json.dumps({
            "model": {
                **BUILTIN_MODELS[0].as_dict(), "cache_dir": str(root / "model"),
                "source": "arbitrary/repository",
            },
        }))
        result = subprocess.run(
            [sys.executable, str(worker), "install", "--config", str(config)],
            capture_output=True, text=True, timeout=20,
        )
    assert result.returncode == 1
    event = json.loads(result.stdout)
    assert event["event"] == "progress" and event["phase"] == "error"
    assert "curated model field" in event["message"]


def test_packaged_bootstrap_assets_are_copied_to_versioned_app_data() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        assets = root / "assets"
        assets.mkdir()
        build_worker_zipapp(assets / "vision_worker.pyz")
        uv = assets / ("uv.exe" if os.name == "nt" else "uv")
        uv.write_bytes(b"fake bundled uv")
        with mock.patch.object(bootstrap, "bundled_asset_dir", return_value=assets):
            installed_uv, worker = bootstrap.materialize_bootstrap(root / "data")
        assert installed_uv.read_bytes() == b"fake bundled uv"
        assert worker.name.startswith("vision-worker-") and worker.suffix == ".pyz"


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} vision service tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
