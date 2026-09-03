"""TOML configuration for independently deployed vision workers."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .frames import DEFAULT_MAX_FRAME_BYTES


@dataclass(frozen=True)
class BrokerSettings:
    host: str
    port: int = 1883
    keepalive: int = 60
    topic_root: str = "desk_buddy"
    username: str | None = None
    password: str | None = None
    tls: bool = False
    ca_file: str | None = None


@dataclass(frozen=True)
class WorkerSettings:
    worker_id: str
    worker_kind: str
    client_id: str
    device: str
    artifact_timeout_seconds: float
    max_frame_bytes: int
    broker: BrokerSettings
    models: dict[str, dict[str, Any]]


def load_worker_settings(path: str | Path) -> WorkerSettings:
    source = Path(path).expanduser().resolve()
    with source.open("rb") as handle:
        raw = tomllib.load(handle)
    mqtt = raw.get("mqtt", {})
    worker = raw.get("worker", {})
    models = raw.get("models", {})
    if not isinstance(mqtt, dict) or not isinstance(worker, dict) or not isinstance(models, dict):
        raise ValueError("worker config requires [mqtt], [worker], and [models] tables")
    host = str(mqtt.get("host") or "").strip()
    worker_id = str(worker.get("worker_id") or "").strip()
    worker_kind = str(worker.get("worker_kind") or "").strip()
    if not host or not worker_id or not worker_kind:
        raise ValueError("mqtt.host, worker.worker_id, and worker.worker_kind are required")
    username_env = str(mqtt.get("username_env") or "DESK_BUDDY_MQTT_USERNAME")
    password_env = str(mqtt.get("password_env") or "DESK_BUDDY_MQTT_PASSWORD")
    ca_file = mqtt.get("ca_file")
    if ca_file:
        candidate = Path(str(ca_file)).expanduser()
        ca_file = str(candidate if candidate.is_absolute() else (source.parent / candidate).resolve())
    return WorkerSettings(
        worker_id=worker_id,
        worker_kind=worker_kind,
        client_id=str(worker.get("client_id") or worker_id),
        device=str(worker.get("device") or "auto"),
        artifact_timeout_seconds=float(worker.get("artifact_timeout_seconds", 120.0)),
        max_frame_bytes=int(worker.get("max_frame_bytes", DEFAULT_MAX_FRAME_BYTES)),
        broker=BrokerSettings(
            host=host,
            port=int(mqtt.get("port", 1883)),
            keepalive=int(mqtt.get("keepalive", 60)),
            topic_root=str(mqtt.get("topic_root") or "desk_buddy").strip().strip("/"),
            username=os.getenv(username_env),
            password=os.getenv(password_env),
            tls=bool(mqtt.get("tls", False)),
            ca_file=str(ca_file) if ca_file else None,
        ),
        models={str(model_id): dict(config) for model_id, config in models.items() if isinstance(config, dict)},
    )

