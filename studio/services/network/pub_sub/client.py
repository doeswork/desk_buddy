"""Studio's own MQTT connection: publish commands, and read replies back.

`traffic.py`'s `TrafficRecorder` subscribes to `#` and writes every message to
history for the debug tray — it never sends anything. This is the other half:
one shared connection any part of Studio can use to publish a command and
wait for a specific reply, without opening a socket of its own.

Studio connects with the account for whichever broker is in use — its
own record for a managed broker, the recorded system account otherwise — the
same way the recorder does, and follows the broker the same way — reconcile()
connects or disconnects to match whether Studio's own broker is up.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from threading import Lock
from typing import Callable

from ..broker.finder import studio_credentials, studio_endpoint

Callback = Callable[[str, dict], None]


class MqttClient:
    """One shared paho connection for sending commands and reading replies."""

    def __init__(self) -> None:
        self._client = None
        self._port = 0
        self._status = "Broker is not running"
        self._lock = Lock()
        # topic filter -> callbacks registered on it. A plain list rather than
        # a set: callbacks are usually bound methods/closures, which are not
        # hashable in a way that would dedupe usefully anyway.
        self._subscribers: dict[str, list[Callback]] = defaultdict(list)
        self._host = ""

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def running(self) -> bool:
        return self._client is not None

    def reconcile(self) -> None:
        """Start or stop the connection to match the broker Studio uses."""
        # Where the machine's broker is, asked of the service rather than
        # assumed: it binds one interface, so "up" does not mean loopback
        # reaches it.
        host, port = studio_endpoint()
        if port and (
            self._client is None or port != self._port or host != self._host
        ):
            self.start(port, host)
        elif not port and self._client is not None:
            self.stop()
        elif not port:
            self._set_status("Broker is not running")

    def start(self, port: int, host: str = "") -> None:
        self.stop()
        host = host or "127.0.0.1"
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            self._set_status("MQTT client unavailable (paho-mqtt is missing)")
            return

        name, password = studio_credentials()
        if not name:
            self._set_status(
                "No broker account yet — set one on Network → Broker."
            )
            return
        try:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id="desk-buddy-studio-commands",
                protocol=mqtt.MQTTv311,
            )
            client.username_pw_set(name, password)
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            client.reconnect_delay_set(min_delay=1, max_delay=10)
            client.connect_async(host, port, keepalive=30)
            self._client = client
            self._port = port
            self._host = host
            self._set_status(f"Connecting to broker on {host}:{port}…")
            client.loop_start()
        except (OSError, ValueError) as error:
            self._client = None
            self._port = 0
            self._host = ""
            self._set_status(f"Could not start the command client: {error}")

    def stop(self) -> None:
        client = self._client
        self._client = None
        self._port = 0
        self._host = ""
        if client is not None:
            try:
                client.disconnect()
                client.loop_stop()
            except (OSError, RuntimeError):
                pass

    # ---- sending -----------------------------------------------------------
    def publish(self, topic: str, payload: dict, *, qos: int = 0) -> str:
        """Publish a JSON command. Returns the action_id used.

        Fills in `sender` and `action_id` when the caller did not supply
        them, per MQTT_SPEC.md §2 — every request needs both to be traceable
        and correlatable, and generating them here means callers never repeat
        that bookkeeping.
        """
        body = dict(payload)
        body.setdefault("sender", "studio")
        action_id = body.setdefault("action_id", uuid.uuid4().hex)

        if self._client is not None:
            self._client.publish(topic, json.dumps(body), qos=qos)
        return action_id

    # ---- receiving -----------------------------------------------------------
    def subscribe(self, topic: str, callback: Callback) -> Callable[[], None]:
        """Call `callback(topic, payload)` for every JSON message on `topic`.

        Returns an unsubscribe function. Many callers can each wait on their
        own topic/action_id without opening their own connection — this is
        what lets a calibration step and the logs viewer share one socket.

        **`callback` runs on paho's network thread, not the GUI thread.** It
        must not touch a widget: creating, deleting or re-parenting one from
        here segfaults the process, which is exactly what a base-rotation
        run did while the main thread was inside QBoxLayout::setGeometry. A
        UI caller hands the payload over with a queued signal first — see
        `ReplyBridge` in `ui/workspaces/calibration/step.py`.
        """
        with self._lock:
            self._subscribers[topic].append(callback)
            if self._client is not None:
                self._client.subscribe(topic, qos=1)

        def unsubscribe() -> None:
            with self._lock:
                callbacks = self._subscribers.get(topic)
                if callbacks and callback in callbacks:
                    callbacks.remove(callback)

        return unsubscribe

    # ---- paho callbacks ------------------------------------------------------
    def _on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        if reason_code != 0:
            self._set_status(f"Connection refused: {reason_code}")
            return
        with self._lock:
            topics = list(self._subscribers.keys())
        for topic in topics:
            client.subscribe(topic, qos=1)
        self._set_status(f"Connected on {self._host}:{self._port}")

    def _on_disconnect(
        self, _client, _userdata, _flags, reason_code, _properties
    ) -> None:
        if self._client is not None:
            self._set_status(f"Disconnected ({reason_code}); retrying…")

    def _on_message(self, _client, _userdata, message) -> None:
        try:
            payload = json.loads(message.payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return

        with self._lock:
            callbacks = list(self._subscribers.get(message.topic, ()))
        for callback in callbacks:
            callback(message.topic, payload)

    def _set_status(self, status: str) -> None:
        with self._lock:
            self._status = status


_instance: MqttClient | None = None


def mqtt_client() -> MqttClient:
    global _instance
    if _instance is None:
        _instance = MqttClient()
    return _instance
