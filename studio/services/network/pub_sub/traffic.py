"""Subscribe to Studio's broker and persist every MQTT publication."""

from __future__ import annotations

import sqlite3
from threading import Lock

from ....models.data.mqtt_messages import MqttMessages, mqtt_messages
from ..broker.finder import studio_credentials, studio_endpoint


class TrafficRecorder:
    """Follow the broker lifecycle without putting MQTT work in a widget."""

    def __init__(self, store: MqttMessages | None = None) -> None:
        self.store = store if store is not None else mqtt_messages()
        self._client = None
        self._port = 0
        self._host = ""
        self._credentials = ("", "")
        self._status = "Broker is not running"
        self._lock = Lock()

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def running(self) -> bool:
        return self._client is not None

    def reconcile(self) -> None:
        """Start or stop recording to match the broker Studio uses."""
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
            self._set_status("MQTT recorder unavailable (paho-mqtt is missing)")
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
                client_id="desk-buddy-studio-debug",
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
        except (OSError, RuntimeError, ValueError) as error:
            self._client = None
            self._port = 0
            self._host = ""
            self._set_status(f"Recorder could not start: {error}")

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

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        if reason_code != 0:
            self._set_status(f"Recorder connection refused: {reason_code}")
            return
        result, _message_id = client.subscribe("#", qos=2)
        if result == 0:
            self._set_status(
                f"Recording all MQTT traffic on {self._host}:{self._port}"
            )
        else:
            self._set_status(f"Recorder could not subscribe (code {result})")

    def _on_disconnect(
        self, _client, _userdata, _flags, reason_code, _properties
    ) -> None:
        # A clean stop already cleared the client. Otherwise paho will retry.
        if self._client is not None:
            self._set_status(f"Recorder disconnected ({reason_code}); retrying…")

    def _on_message(self, _client, _userdata, message) -> None:
        try:
            self.store.append(
                message.topic,
                message.payload,
                qos=message.qos,
                retained=message.retain,
            )
        except (OSError, sqlite3.Error, ValueError) as error:
            self._set_status(f"Could not save MQTT traffic: {error}")

    def _set_status(self, status: str) -> None:
        with self._lock:
            self._status = status
