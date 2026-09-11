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
from threading import RLock
from typing import Callable

from ..broker.finder import studio_credentials, studio_endpoint

Callback = Callable[[str, dict], None]
RawCallback = Callable[[str, bytes], None]


class MqttClient:
    """One shared paho connection for sending commands and reading replies."""

    def __init__(self) -> None:
        self._client = None
        # A detached replacement briefly exists beside the process it is
        # replacing.  A per-instance id keeps the broker from making those
        # two clients repeatedly disconnect and reconnect each other.
        self._client_id = f"desk-buddy-studio-commands-{uuid.uuid4().hex[:12]}"
        self._port = 0
        self._status = "Broker is not running"
        self._lock = RLock()
        self._connected = False
        self._connection_revision = 0
        self._acknowledged: set[str] = set()
        self._subscription_mids: dict[int, str] = {}
        self._connection_callbacks: list[Callable[[bool], None]] = []
        self._subscription_callbacks: list[Callable[[], None]] = []
        # topic filter -> callbacks registered on it. A plain list rather than
        # a set: callbacks are usually bound methods/closures, which are not
        # hashable in a way that would dedupe usefully anyway.
        self._subscribers: dict[str, list[Callback]] = defaultdict(list)
        # Kept separate because photo channels carry opaque binary frames while
        # command/event/service channels carry JSON.
        self._raw_subscribers: dict[str, list[RawCallback]] = defaultdict(list)
        self._host = ""
        self._credentials = ("", "")

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def running(self) -> bool:
        return self._client is not None

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    def subscriptions_ready(self, topics) -> bool:
        with self._lock:
            return self._connected and set(topics) <= self._acknowledged

    @property
    def connection_revision(self) -> int:
        with self._lock:
            return self._connection_revision

    def watch_connection(self, callback) -> Callable[[], None]:
        return self._watch(self._connection_callbacks, callback)

    def watch_subscriptions(self, callback) -> Callable[[], None]:
        return self._watch(self._subscription_callbacks, callback)

    def _watch(self, callbacks, callback):
        with self._lock:
            callbacks.append(callback)
        def cancel():
            with self._lock:
                if callback in callbacks:
                    callbacks.remove(callback)
        return cancel

    def _connection_changed(self, connected: bool) -> None:
        with self._lock:
            changed = connected != self._connected
            self._connected = connected
            if changed:
                self._connection_revision += 1
            self._acknowledged.clear()
            self._subscription_mids.clear()
            callbacks = tuple(self._connection_callbacks) if changed else ()
        for callback in callbacks:
            callback(connected)

    def _subscribe_topic(self, topic: str) -> None:
        # Caller holds the lock, including while the MID is registered. This
        # prevents a fast network-thread SUBACK racing the registration.
        if (self._connected and topic not in self._acknowledged
                and topic not in self._subscription_mids.values()):
            rc, mid = self._client.subscribe(topic, qos=1)
            if rc == 0:
                self._subscription_mids[mid] = topic

    def reconcile(self) -> None:
        """Start or stop the connection to match the broker Studio uses."""
        # Where the machine's broker is, asked of the service rather than
        # assumed: it binds one interface, so "up" does not mean loopback
        # reaches it.
        host, port = studio_endpoint()
        if port and (
            self._client is None or port != self._port or host != self._host
            or studio_credentials() != self._credentials
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
        self._credentials = (name, password)
        if not name:
            self._set_status(
                "No broker account yet — set one on Network → Broker."
            )
            return
        try:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=self._client_id,
                protocol=mqtt.MQTTv311,
            )
            client.username_pw_set(name, password)
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            client.on_subscribe = self._on_subscribe
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
        self._connection_changed(False)
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

    def publish_raw(self, topic: str, payload: bytes, *, qos: int = 1) -> bool:
        """Publish opaque bytes without JSON encoding. True when queued."""
        client = self._client
        if client is None or not isinstance(payload, bytes):
            return False
        result = client.publish(topic, payload, qos=qos)
        return not bool(getattr(result, "rc", 0))

    def publish_checked(self, topic: str, payload: dict, *, qos: int = 1) -> bool:
        """Queue once on a live connection; success is not a delivery receipt."""
        with self._lock:
            if not self._connected or self._client is None:
                return False
            result = self._client.publish(topic, json.dumps(payload), qos=qos)
            return not bool(getattr(result, "rc", 0))

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
            self._subscribe_topic(topic)

        def unsubscribe() -> None:
            with self._lock:
                callbacks = self._subscribers.get(topic)
                if callbacks and callback in callbacks:
                    callbacks.remove(callback)
                if not callbacks:
                    self._subscribers.pop(topic, None)
                if (
                    self._client is not None
                    and topic not in self._subscribers
                    and topic not in self._raw_subscribers
                ):
                    self._client.unsubscribe(topic)
                    self._acknowledged.discard(topic)
                    self._subscription_mids = {mid: name for mid, name in self._subscription_mids.items() if name != topic}

        return unsubscribe

    def subscribe_raw(
        self, topic: str, callback: RawCallback
    ) -> Callable[[], None]:
        """Call ``callback(topic, bytes)`` before any JSON decoding.

        Like :meth:`subscribe`, the callback runs on paho's network thread.
        UI consumers must cross a queued Qt signal before touching widgets.
        """
        with self._lock:
            self._raw_subscribers[topic].append(callback)
            self._subscribe_topic(topic)

        def unsubscribe() -> None:
            with self._lock:
                callbacks = self._raw_subscribers.get(topic)
                if callbacks and callback in callbacks:
                    callbacks.remove(callback)
                if not callbacks:
                    self._raw_subscribers.pop(topic, None)
                if (
                    self._client is not None
                    and topic not in self._subscribers
                    and topic not in self._raw_subscribers
                ):
                    self._client.unsubscribe(topic)
                    self._acknowledged.discard(topic)
                    self._subscription_mids = {mid: name for mid, name in self._subscription_mids.items() if name != topic}

        return unsubscribe

    # ---- paho callbacks ------------------------------------------------------
    def _on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        if client is not self._client:
            return
        if reason_code != 0:
            self._set_status(f"Connection refused: {reason_code}")
            return
        self._connection_changed(True)
        with self._lock:
            topics = list(set(self._subscribers) | set(self._raw_subscribers))
            for topic in topics:
                self._subscribe_topic(topic)
        self._set_status(f"Connected on {self._host}:{self._port}")

    def _on_subscribe(self, client, _userdata, mid, reason_codes, _properties) -> None:
        if client is not self._client:
            return
        with self._lock:
            topic = self._subscription_mids.pop(mid, None)
            if topic is None:
                return
            accepted = bool(reason_codes) and all(
                getattr(code, "value", code) < 128 for code in reason_codes
            )
            if accepted:
                self._acknowledged.add(topic)
            else:
                self._status = f"Broker rejected subscription to {topic}"
            callbacks = tuple(self._subscription_callbacks)
        for callback in callbacks:
            callback()

    def _on_disconnect(
        self, _client, _userdata, _flags, reason_code, _properties
    ) -> None:
        if _client is not self._client:
            return
        self._connection_changed(False)
        if self._client is not None:
            self._set_status(f"Disconnected ({reason_code}); retrying…")

    def _on_message(self, _client, _userdata, message) -> None:
        raw = bytes(message.payload)
        with self._lock:
            raw_callbacks = list(self._raw_subscribers.get(message.topic, ()))
            callbacks = list(self._subscribers.get(message.topic, ()))
        for callback in raw_callbacks:
            callback(message.topic, raw)

        if not callbacks:
            return

        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return

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
