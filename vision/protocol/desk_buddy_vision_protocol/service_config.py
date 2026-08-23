from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BrokerSettings:
    host: str
    port: int
    keepalive: int
    topic_root: str
    username: str | None
    password: str | None
    tls: bool
    ca_file: str | None
    cert_file: str | None
    key_file: str | None


@dataclass(frozen=True)
class ServiceSettings:
    service_id: str
    service_kind: str
    client_id: str
    compute_device: str
    artifact_timeout_seconds: float
    artifact_chunk_size: int
    broker: BrokerSettings
    models: dict[str, dict[str, Any]]


def _path(value: Any, base: Path) -> str | None:
    if value in (None, ""):
        return None
    path = Path(str(value)).expanduser()
    return str(path if path.is_absolute() else (base / path).resolve())


def load_service_settings(path: str | Path) -> ServiceSettings:
    source = Path(path).expanduser().resolve()
    with source.open("rb") as handle:
        raw = tomllib.load(handle)
    mqtt = raw.get("mqtt")
    service = raw.get("service")
    models = raw.get("models", {})
    if not isinstance(mqtt, dict) or not isinstance(service, dict) or not isinstance(models, dict):
        raise ValueError("config requires [mqtt], [service], and [models] tables")
    host = str(mqtt.get("host") or "").strip()
    service_id = str(service.get("service_id") or "").strip()
    service_kind = str(service.get("service_kind") or "").strip()
    if not host or not service_id or not service_kind:
        raise ValueError("mqtt.host, service.service_id, and service.service_kind are required")
    username_env = str(mqtt.get("username_env", "DESK_BUDDY_MQTT_USERNAME"))
    password_env = str(mqtt.get("password_env", "DESK_BUDDY_MQTT_PASSWORD"))
    return ServiceSettings(
        service_id=service_id,
        service_kind=service_kind,
        client_id=str(service.get("client_id", service_id)),
        compute_device=str(service.get("compute_device", "auto")),
        artifact_timeout_seconds=float(service.get("artifact_timeout_seconds", 120.0)),
        artifact_chunk_size=int(service.get("artifact_chunk_size", 4096)),
        broker=BrokerSettings(
            host=host,
            port=int(mqtt.get("port", 8883)),
            keepalive=int(mqtt.get("keepalive", 60)),
            topic_root=str(mqtt.get("topic_root", "desk_buddy")).strip().strip("/"),
            username=os.getenv(username_env),
            password=os.getenv(password_env),
            tls=bool(mqtt.get("tls", True)),
            ca_file=_path(mqtt.get("ca_file"), source.parent),
            cert_file=_path(mqtt.get("cert_file"), source.parent),
            key_file=_path(mqtt.get("key_file"), source.parent),
        ),
        models={str(model_id): dict(values) for model_id, values in models.items() if isinstance(values, dict)},
    )

