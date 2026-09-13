"""Capture lifecycle and isolated broker integration; no model downloads.

QT_QPA_PLATFORM=offscreen python -m studio.services.vision.reliability_tests
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from .tests import (
    JPEG, FakeClient, _app, temporary_manager, service, run_one_job,
    DEFAULT_MODEL_ID, normalize_batch,
)
from .frames import encode_frame
from ..network.pub_sub.client import MqttClient
from .worker_runtime.desk_buddy_vision_worker.service import RESULT_TOPIC


def photo(action_id="a", jpeg=JPEG):
    return encode_frame({
        "schema": "desk_buddy.photo.v1", "sender": "firmware", "photo": "sending_photo",
        "content_type": "image/jpeg", "type": "detect_object", "action_id": action_id,
        "width": 100, "height": 50, "size": len(jpeg),
    }, jpeg)


def request(worker, action_id="a", robot="black"):
    worker._receive_robot_request(f"{robot}/commands", json.dumps({
        "sender": "studio", "action_id": action_id, "action": "detect_object",
        "phrase": "mug", "use_model": False,
    }).encode())


def ready(manager):
    manager._replace(active_model_id=DEFAULT_MODEL_ID, process_state="running", mqtt_state="ready")


def results(client):
    return [body for topic, body, _ in client.sent if topic.endswith("/vision") or topic == RESULT_TOPIC]


def test_subscription_gate_failure_and_no_republication():
    fake = FakeClient()
    fake.acknowledged = False
    with temporary_manager(client=fake) as (manager, client):
        ready(manager)
        action_id = manager.detect_robot("black", "mug")
        assert action_id and not client.json_sent
        client.acknowledged = True
        for callback in client.subscription_watchers:
            callback()
        _app.processEvents()
        assert len(client.json_sent) == 1
        manager._try_send_detection()
        assert len(client.json_sent) == 1
        client.connected = False
        manager._on_connection(False)
        assert not manager.state.pending_detection and "connection lost" in manager.state.error
        manager._on_connection(True)
        assert len(client.json_sent) == 1
    fake = FakeClient()
    fake.accept_publish = False
    with temporary_manager(client=fake) as (manager, client):
        ready(manager)
        assert not manager.detect_robot("black", "mug")
        assert "Could not publish" in manager.state.error
        assert not manager._detection_timer.isActive()
    fake = FakeClient()
    fake.acknowledged = False
    with temporary_manager(client=fake) as (manager, client):
        ready(manager)
        manager.detect_robot("black", "mug")
        manager._preparation_timer.timeout.emit()
        assert "10 seconds" in manager.state.error and not client.json_sent


def test_actual_command_byte_limit_and_long_prompts():
    with temporary_manager() as (manager, client):
        ready(manager)
        assert manager.detect_robot("black", "m" * 1000)
        manager._detection_timed_out()
        assert not manager.detect_robot("black", "m" * 6100)
        assert "6144-byte" in manager.state.error
        assert not manager.detect_robot("black", "茶" * 1100)
        assert len(client.json_sent) == 1


def test_stale_disconnect_does_not_cancel_new_session_request():
    with temporary_manager() as (manager, client):
        ready(manager)
        client.connection_revision = 3
        action_id = manager.detect_robot("black", "mug")
        manager._on_connection(False)  # Queued notification predating this request.
        assert manager.state.pending_detection == action_id
        client.connection_revision = 5  # A disconnect/reconnect after publication.
        manager._on_connection(False)
        assert not manager.state.pending_detection


def test_photo_result_order_progress_and_manual_retry():
    with temporary_manager() as (manager, client):
        ready(manager)
        images, delivered = [], []
        manager.photo_received.connect(images.append)
        manager.detection_result.connect(delivered.append)
        action_id = manager.detect_robot("black", "mug")
        assert images == [b""]
        manager._on_result("black/events", {"sender": "firmware", "action_id": action_id, "status": "in_progress"})
        assert manager.state.detection_stage == "capturing"
        manager._hint_timer.timeout.emit()
        assert manager.state.detection_delayed
        result = {"sender": "visual_ai", "action_id": action_id, "status": "completed"}
        manager._on_result("black/vision", result)
        assert manager.state.pending_detection and not delivered
        manager._on_raw("black/photos", photo("old"))
        assert manager.state.pending_detection and images == [b""]
        manager._on_raw("black/photos", photo(action_id))
        assert not manager.state.pending_detection and images[-1] == JPEG and delivered == [result]
        second = manager.detect_robot("black", "mug")
        assert second != action_id
        manager._on_result("black/vision", {**result, "action_id": second})
        manager._detection_timed_out()
        assert "Incomplete preview" in manager.state.error
        manager._on_raw("black/photos", photo(second))
        assert delivered == [result]


def test_duplicates_unrelated_and_correlated_invalid_frames():
    worker, client = service()
    request(worker)
    request(worker)
    worker._receive_robot_frame("black/photos", photo("old"))
    worker._receive_robot_frame("black/photos", b"not framed")
    assert len(worker.requests) == 1 and not results(client)
    worker._receive_robot_frame("black/photos", photo())
    worker._receive_robot_frame("black/photos", photo())
    assert worker.jobs.qsize() == 1
    run_one_job(worker)
    request(worker)
    worker._receive_robot_frame("black/photos", photo())
    assert len(results(client)) == 1 and not worker.requests and worker.jobs.empty()
    request(worker, "bad")
    worker._receive_robot_frame("black/photos", photo("bad").replace(b"\xff\xd9}", b"broken}"))
    assert results(client)[-1]["error"]["code"] == "invalid_image"
    assert not worker.requests


def test_bounded_admission_queue_and_terminal_cache():
    worker, client = service()
    for i in range(9):
        robot = f"robot{i}"
        worker.command_routes[f"{robot}/commands"] = {"robot": robot, "result_topic": f"{robot}/vision"}
        request(worker, str(i), robot)
    assert len(worker.requests) == 8
    assert results(client)[-1]["error"]["code"] == "detector_busy"
    for i in range(300):
        request(worker, f"extra{i}", "robot0")
    assert len(worker.requests) == 8 and len(worker._terminal) == 256
    worker._receive_local(encode_frame({"schema": "desk_buddy.vision.detect-request.v1", "action_id": "local", "prompt": "mug"}, JPEG))
    assert len(worker.requests) == 8 and results(client)[-1]["error"]["code"] == "detector_busy"
    for record in worker.requests.values():
        record.received -= 121
    worker._expire_requests()
    assert not worker.requests
    worker, client = service()
    request(worker)
    worker._receive_robot_frame("black/photos", photo())
    worker._receive_local(encode_frame({"schema": "desk_buddy.vision.detect-request.v1", "action_id": "local", "prompt": "mug"}, JPEG))
    assert worker.jobs.qsize() == 1
    assert results(client)[-1]["error"]["code"] == "detector_busy"
    run_one_job(worker)


def test_queued_expiry_and_terminal_cache_expiry():
    worker, client = service()
    request(worker)
    worker._receive_robot_frame("black/photos", photo())
    worker.requests[("black/vision", "a")].received -= 121
    with mock.patch.object(worker.detector, "detect", side_effect=AssertionError("expired inference ran")):
        run_one_job(worker)
    assert results(client)[-1]["error"]["code"] == "detection_timeout"
    worker._terminal[("black/vision", "a")] -= 121
    request(worker)
    assert len(worker.requests) == 1


def test_running_expiry_and_firmware_failure_suppress_late_inference():
    for firmware_failure in (False, True):
        worker, client = service()
        entered, release = threading.Event(), threading.Event()
        original = worker.detector.detect
        def detect(jpeg, prompt):
            entered.set()
            assert release.wait(3)
            return original(jpeg, prompt)
        worker.detector.detect = detect
        request(worker)
        worker._receive_robot_frame("black/photos", photo())
        thread = threading.Thread(target=worker._work_loop, daemon=True)
        thread.start()
        assert entered.wait(2)
        if firmware_failure:
            worker._receive_robot_event("black/events", json.dumps({
                "sender": "firmware", "action_id": "a", "status": "failed",
                "error": {"code": "photo_publish_failed", "message": "lost"},
            }).encode())
        else:
            with worker._lock:
                worker.requests[("black/vision", "a")].received -= 121
            worker._expire_requests()
        release.set()
        worker.jobs.put(None)
        thread.join(3)
        assert not thread.is_alive()
        assert len(results(client)) == 1 and results(client)[0]["status"] == "failed"


def test_completion_timeout_race_is_atomic():
    for _ in range(20):
        worker, client = service()
        request(worker)
        record = worker.requests[("black/vision", "a")]
        record.received -= 121
        barrier = threading.Barrier(3)
        def finish():
            barrier.wait()
            worker._publish_result("black/vision", {"action_id": "a", "status": "completed"}, record=record)
        def expire():
            barrier.wait()
            worker._expire_requests()
        threads = [threading.Thread(target=finish), threading.Thread(target=expire)]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join()
        assert len(results(client)) == 1 and not worker.requests


def test_mqtt_callbacks_require_suback_and_skip_raw_json():
    client = MqttClient()
    wire = SimpleNamespace(subscribe=mock.Mock(return_value=(0, 42)), publish=mock.Mock(return_value=SimpleNamespace(rc=0)))
    client._client = wire
    seen = []
    client.subscribe_raw("black/photos", lambda topic, payload: seen.append(payload))
    client._on_connect(wire, None, None, 0, None)
    assert client.connected and not client.subscriptions_ready(["black/photos"])
    client._on_subscribe(wire, None, 42, [1], None)
    assert client.subscriptions_ready(["black/photos"])
    with mock.patch("studio.services.network.pub_sub.client.json.loads", side_effect=AssertionError("raw decoded")):
        client._on_message(wire, None, SimpleNamespace(topic="black/photos", payload=photo()))
    assert seen == [photo()]
    client._on_disconnect(wire, None, None, 1, None)
    assert not client.publish_checked("black/commands", {})
    assert not client.subscriptions_ready(["black/photos"])
    client._on_connect(wire, None, None, 0, None)
    client._on_subscribe(wire, None, 42, [128], None)
    assert not client.subscriptions_ready(["black/photos"])


def test_worker_requires_all_subacks_and_cancels_on_disconnect():
    worker, client = service()
    worker.loaded = True
    worker._on_connect(client, None, None, 0, None)
    mids = tuple(worker._subscription_mids)
    for mid in mids[:-1]:
        worker._on_subscribe(client, None, mid, [1], None)
    assert not worker._subscriptions_ready
    worker._on_subscribe(client, None, mids[-1], [1], None)
    assert worker._subscriptions_ready
    request(worker)
    worker._on_disconnect(client, None, None, 1, None)
    assert not worker._subscriptions_ready and not worker.requests
    assert results(client)[-1]["error"]["code"] == "connection_lost"


def wait_until(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _app.processEvents()
        if predicate(): return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for test condition")


def test_isolated_broker_capture_fanout_and_reconnect():
    import paho.mqtt.client as mqtt
    from ..network.pub_sub import client as client_module
    broker = shutil.which("mosquitto")
    assert broker, "Install Mosquitto to run the capture integration test"
    with tempfile.TemporaryDirectory(prefix="desk-buddy-mqtt-") as directory:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        config = Path(directory) / "mosquitto.conf"
        config.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\nmax_packet_size 8389632\n")
        log = open(Path(directory) / "broker.log", "w+")
        process = subprocess.Popen([broker, "-c", str(config)], stdout=log, stderr=log)
        clients = []
        worker, _ = service()
        worker.loaded = True
        seen_images, commands, observed = [], [], []
        jpeg = b"\xff\xd8" + bytes(range(256)) * 600 + b"\xff\xd9"
        def detect(data, prompt):
            seen_images.append(data)
            return normalize_batch(width=100, height=50, prompt=prompt,
                model_id=worker.detector.model_id, revision=worker.detector.revision,
                detections=({"label": prompt, "score": .9, "box": [10, 10, 50, 40]},))
        worker.detector.detect = detect
        inference = threading.Thread(target=worker._work_loop, daemon=True)
        inference.start()
        try:
            def port_up():
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.05): return True
                except OSError: return False
            wait_until(port_up)
            def mqtt_client():
                client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
                clients.append(client)
                return client
            detector = mqtt_client()
            worker.client = detector
            detector.on_connect = worker._on_connect
            detector.on_subscribe = worker._on_subscribe
            detector.on_disconnect = worker._on_disconnect
            detector.on_message = worker._on_message
            detector.connect("127.0.0.1", port)
            detector.loop_start()
            wait_until(lambda: worker._subscriptions_ready)
            robot = mqtt_client()
            subscribed = threading.Event()
            robot.on_connect = lambda c, *_: c.subscribe("black/commands", qos=0)
            robot.on_subscribe = lambda *_: subscribed.set()
            capture_enabled = [True]
            def capture(c, _userdata, message):
                assert message.topic == "black/commands"
                body = json.loads(message.payload)
                commands.append(body)
                if capture_enabled[0]:
                    c.publish("black/photos", photo(body["action_id"], jpeg), qos=0)
            robot.on_message = capture
            robot.connect("127.0.0.1", port)
            robot.loop_start()
            wait_until(subscribed.is_set)
            observer = mqtt_client()
            observer.on_connect = lambda c, *_: c.subscribe("#")
            observer.on_message = lambda _c, _u, m: observed.append((m.topic, bytes(m.payload)))
            observer.connect("127.0.0.1", port)
            observer.loop_start()
            shared = MqttClient()
            with mock.patch.object(client_module, "studio_credentials", return_value=("test", "test")), \
                 mock.patch.object(shared, "reconcile"):
                shared.start(port, "127.0.0.1")
                try:
                    with temporary_manager(client=shared) as (manager, _):
                        ready(manager)
                        photos, completed = [], []
                        manager.photo_received.connect(photos.append)
                        manager.detection_result.connect(completed.append)
                        first = manager.detect_robot("black", "mug")
                        wait_until(lambda: bool(completed))
                        assert completed[0]["action_id"] == first and completed[0]["status"] == "completed"
                        assert photos[-1] == seen_images[-1] == jpeg
                        assert len(commands) == 1
                        wait_until(lambda: any(topic == "black/photos" for topic, _ in observed))
                        capture_enabled[0] = False
                        second = manager.detect_robot("black", "mug")
                        wait_until(lambda: len(commands) == 2)
                        process.terminate(); process.wait(3)
                        wait_until(lambda: not manager.state.pending_detection)
                        assert "connection" in manager.state.error.lower() or "unavailable" in manager.state.error.lower()
                        process = subprocess.Popen([broker, "-c", str(config)], stdout=log, stderr=log)
                        wait_until(lambda: shared.connected and worker._subscriptions_ready, 10)
                        # Drain reconnect callbacks; no application capture is replayed.
                        wait_until(lambda: robot.is_connected(), 10)
                        assert len(commands) == 2 and second != first
                        capture_enabled[0] = True
                        third = manager.detect_robot("black", "mug")
                        wait_until(lambda: len(completed) == 2)
                        assert completed[-1]["action_id"] == third and len(commands) == 3
                finally:
                    shared.stop()
        finally:
            for client in clients:
                client.disconnect(); client.loop_stop()
            worker.jobs.put(None, timeout=3)
            inference.join(3)
            process.terminate(); process.wait(3)
            log.close()


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} capture reliability tests (including isolated Mosquitto)")


if __name__ == "__main__":
    main()
