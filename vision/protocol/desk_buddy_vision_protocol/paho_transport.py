from __future__ import annotations

import ssl
import threading
from typing import Any, Callable

from .envelopes import encode_json


class PahoServiceTransport:
    """Small paho wrapper shared by isolated service environments."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        client_id: str,
        keepalive: int = 60,
        username: str | None = None,
        password: str | None = None,
        tls: bool = True,
        ca_file: str | None = None,
        cert_file: str | None = None,
        key_file: str | None = None,
    ) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("paho-mqtt is required to run an MQTT service") from exc
        self._mqtt = mqtt
        self._host = host
        self._port = port
        self._keepalive = keepalive
        self._handler: Callable[[str, bytes], None] | None = None
        self._subscriptions: dict[str, int] = {}
        self._connected = threading.Event()
        self._connect_handlers: list[Callable[[], None]] = []
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)
        if username:
            self._client.username_pw_set(username, password)
        if tls:
            self._client.tls_set(
                ca_certs=ca_file,
                certfile=cert_file,
                keyfile=key_file,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    def set_message_handler(self, handler: Callable[[str, bytes], None]) -> None:
        self._handler = handler

    def set_last_will(self, topic: str, payload: dict[str, Any]) -> None:
        self._client.will_set(topic, encode_json(payload), qos=1, retain=True)

    def add_connect_handler(self, handler: Callable[[], None]) -> None:
        self._connect_handlers.append(handler)

    def subscribe(self, topic: str, *, qos: int = 1) -> None:
        self._subscriptions[topic] = qos
        if self._client.is_connected():
            self._client.subscribe(topic, qos=qos)

    def publish(self, topic: str, payload: bytes | str | dict[str, Any], *, qos: int, retain: bool = False) -> bool:
        value = encode_json(payload) if isinstance(payload, dict) else payload
        return self._client.publish(topic, value, qos=qos, retain=retain).rc == self._mqtt.MQTT_ERR_SUCCESS

    def connect(self) -> None:
        self._client.connect(self._host, self._port, self._keepalive)
        self._client.loop_start()
        if not self._connected.wait(10.0):
            raise TimeoutError("timed out waiting for MQTT CONNACK")

    def close(self) -> None:
        try:
            self._client.disconnect()
        finally:
            self._client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code == 0:
            for topic, qos in self._subscriptions.items():
                client.subscribe(topic, qos=qos)
            self._connected.set()
            for handler in self._connect_handlers:
                handler()

    def _on_message(self, client, userdata, message) -> None:
        if self._handler:
            self._handler(str(message.topic), bytes(message.payload))
