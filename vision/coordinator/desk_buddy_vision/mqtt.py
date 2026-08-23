from __future__ import annotations

import json
import logging
import ssl
import threading
from collections.abc import Callable
from typing import Any, Protocol

from desk_buddy_vision_protocol.envelopes import encode_json

from .config import MQTTConfig

LOGGER = logging.getLogger(__name__)
MessageHandler = Callable[[str, bytes], None]


class MQTTTransport(Protocol):
    def subscribe(self, topic: str, *, qos: int = 1) -> None: ...

    def publish(self, topic: str, payload: bytes | str | dict[str, Any], *, qos: int, retain: bool = False) -> bool: ...

    def set_message_handler(self, handler: MessageHandler) -> None: ...

    def connect(self) -> None: ...

    def close(self) -> None: ...


class PahoTransport:
    def __init__(self, config: MQTTConfig) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:  # pragma: no cover - depends on installed service environment
            raise RuntimeError("paho-mqtt is required to run the coordinator") from exc
        self._mqtt = mqtt
        self._config = config
        self._handler: MessageHandler | None = None
        self._subscriptions: dict[str, int] = {}
        self._connected = threading.Event()
        self._connect_handlers: list[Callable[[], None]] = []
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=config.client_id, clean_session=True)
        if config.username:
            self._client.username_pw_set(config.username, config.password)
        if config.tls:
            self._client.tls_set(
                ca_certs=str(config.ca_file) if config.ca_file else None,
                certfile=str(config.cert_file) if config.cert_file else None,
                keyfile=str(config.key_file) if config.key_file else None,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

    def set_message_handler(self, handler: MessageHandler) -> None:
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
        if isinstance(payload, dict):
            value: bytes | str = encode_json(payload)
        else:
            value = payload
        info = self._client.publish(topic, value, qos=qos, retain=retain)
        return info.rc == self._mqtt.MQTT_ERR_SUCCESS

    def connect(self) -> None:
        self._client.connect(self._config.host, self._config.port, self._config.keepalive)
        self._client.loop_start()
        if not self._connected.wait(10.0):
            raise TimeoutError("timed out waiting for MQTT CONNACK")

    def close(self) -> None:
        try:
            self._client.disconnect()
        finally:
            self._client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            LOGGER.error("MQTT connection failed: %s", reason_code)
            return
        for topic, qos in self._subscriptions.items():
            client.subscribe(topic, qos=qos)
        self._connected.set()
        for handler in self._connect_handlers:
            handler()
        LOGGER.info("MQTT connected and subscribed to %d topic filters", len(self._subscriptions))

    def _on_message(self, client, userdata, message) -> None:
        if self._handler is None:
            return
        try:
            self._handler(str(message.topic), bytes(message.payload))
        except Exception:
            LOGGER.exception("unhandled MQTT callback error for topic %s", message.topic)

    @staticmethod
    def _on_disconnect(client, userdata, disconnect_flags, reason_code, properties) -> None:
        if reason_code != 0:
            LOGGER.warning("unexpected MQTT disconnect: %s", reason_code)
