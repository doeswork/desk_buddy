"""Small paho transport shared by Studio and isolated worker environments."""

from __future__ import annotations

import json
import ssl
import threading
from typing import Any, Callable, Mapping


class PahoTransport:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        client_id: str,
        username: str | None = None,
        password: str | None = None,
        keepalive: int = 60,
        tls: bool = False,
        ca_file: str | None = None,
    ) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise RuntimeError("Install paho-mqtt to use vision services") from exc
        self._mqtt = mqtt
        self._host = host
        self._port = int(port)
        self._keepalive = int(keepalive)
        self._handler: Callable[[str, bytes], None] | None = None
        self._connect_handlers: list[Callable[[], None]] = []
        self._subscriptions: dict[str, int] = {}
        self._connected = threading.Event()
        self._connect_error = ""
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)
        if username:
            self._client.username_pw_set(username, password)
        if tls:
            self._client.tls_set(ca_certs=ca_file, cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    def set_handler(self, handler: Callable[[str, bytes], None]) -> None:
        self._handler = handler

    def add_connect_handler(self, handler: Callable[[], None]) -> None:
        self._connect_handlers.append(handler)

    def set_last_will(self, topic: str, payload: Mapping[str, Any]) -> None:
        self._client.will_set(topic, _json(payload), qos=1, retain=True)

    def subscribe(self, topic: str, *, qos: int = 1) -> None:
        self._subscriptions[topic] = qos
        if self._client.is_connected():
            self._client.subscribe(topic, qos=qos)

    def publish(self, topic: str, payload: bytes | str | Mapping[str, Any], *, qos: int, retain: bool = False) -> bool:
        if isinstance(payload, Mapping):
            value: bytes | str = _json(payload)
        else:
            value = payload
        info = self._client.publish(topic, value, qos=qos, retain=retain)
        return info.rc == self._mqtt.MQTT_ERR_SUCCESS

    def connect(self, timeout: float = 10.0) -> None:
        self._client.connect(self._host, self._port, self._keepalive)
        self._client.loop_start()
        if not self._connected.wait(timeout):
            raise TimeoutError("timed out waiting for MQTT connection")
        if self._connect_error:
            raise ConnectionError(self._connect_error)

    def close(self) -> None:
        try:
            self._client.disconnect()
        finally:
            self._client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            self._connect_error = f"MQTT connection refused: {reason_code}"
            self._connected.set()
            return
        self._connect_error = ""
        for topic, qos in self._subscriptions.items():
            client.subscribe(topic, qos=qos)
        self._connected.set()
        for handler in tuple(self._connect_handlers):
            handler()

    def _on_message(self, client, userdata, message) -> None:
        if self._handler is not None:
            self._handler(str(message.topic), bytes(message.payload))


def _json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), separators=(",", ":"), sort_keys=True).encode("utf-8")
