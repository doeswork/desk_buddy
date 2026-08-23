from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
for path in (
    ROOT / "protocol",
    ROOT / "coordinator",
    ROOT / "services" / "detector_huggingface",
    ROOT / "services" / "depth_huggingface",
    ROOT / "services" / "planner_training",
    ROOT / "client",
):
    sys.path.insert(0, str(path))

from desk_buddy_vision_protocol.envelopes import encode_json  # noqa: E402


def topic_matches(topic: str, pattern: str) -> bool:
    topic_parts = topic.split("/")
    pattern_parts = pattern.split("/")
    for index, expected in enumerate(pattern_parts):
        if expected == "#":
            return True
        if index >= len(topic_parts):
            return False
        if expected != "+" and expected != topic_parts[index]:
            return False
    return len(topic_parts) == len(pattern_parts)


@dataclass(frozen=True)
class Published:
    client_id: str
    topic: str
    payload: bytes
    qos: int
    retain: bool

    def json(self) -> dict[str, Any]:
        return json.loads(self.payload.decode("utf-8"))


class FakeBroker:
    def __init__(self) -> None:
        self.clients: list[FakeTransport] = []
        self.published: list[Published] = []
        self._lock = threading.RLock()

    def client(self, client_id: str) -> "FakeTransport":
        result = FakeTransport(self, client_id)
        self.clients.append(result)
        return result

    def publish(self, source: "FakeTransport", topic: str, payload: bytes, qos: int, retain: bool) -> bool:
        message = Published(source.client_id, topic, payload, qos, retain)
        with self._lock:
            self.published.append(message)
            recipients = list(self.clients)
        for client in recipients:
            if client.handler and any(topic_matches(topic, pattern) for pattern in client.subscriptions):
                client.handler(topic, payload)
        return True

    def find(self, predicate: Callable[[Published], bool]) -> list[Published]:
        with self._lock:
            return [message for message in self.published if predicate(message)]


class FakeTransport:
    def __init__(self, broker: FakeBroker, client_id: str) -> None:
        self.broker = broker
        self.client_id = client_id
        self.subscriptions: dict[str, int] = {}
        self.handler: Callable[[str, bytes], None] | None = None

    def set_message_handler(self, handler: Callable[[str, bytes], None]) -> None:
        self.handler = handler

    def subscribe(self, topic: str, *, qos: int = 1) -> None:
        self.subscriptions[topic] = qos

    def publish(self, topic: str, payload: bytes | str | dict[str, Any], *, qos: int, retain: bool = False) -> bool:
        if isinstance(payload, dict):
            raw = encode_json(payload)
        elif isinstance(payload, str):
            raw = payload.encode("utf-8")
        else:
            raw = bytes(payload)
        return self.broker.publish(self, topic, raw, qos, retain)

    def connect(self) -> None:
        return None

    def close(self) -> None:
        return None


def wait_until(predicate: Callable[[], Any], *, timeout: float = 5.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise TimeoutError("condition was not met")
