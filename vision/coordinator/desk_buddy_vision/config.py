from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MQTTConfig:
    host: str
    port: int = 8883
    keepalive: int = 60
    topic_root: str = "desk_buddy"
    client_id: str = "desk-buddy-vision-coordinator"
    username_env: str = "DESK_BUDDY_MQTT_USERNAME"
    password_env: str = "DESK_BUDDY_MQTT_PASSWORD"
    tls: bool = True
    ca_file: Path | None = None
    cert_file: Path | None = None
    key_file: Path | None = None

    @property
    def username(self) -> str | None:
        return os.getenv(self.username_env)

    @property
    def password(self) -> str | None:
        return os.getenv(self.password_env)


@dataclass(frozen=True)
class CoordinatorConfig:
    service_id: str
    data_dir: Path
    command_timeout_seconds: float = 30.0
    worker_timeout_seconds: float = 120.0
    artifact_chunk_size: int = 4096
    allow_extrapolated_motion: bool = False
    max_rotation_deg: float = 45.0
    min_distance_mm: float = 0.0
    max_distance_mm: float = 180.0
    min_z_height_mm: float = -30.0
    max_z_height_mm: float = 120.0
    max_rotation_correction_deg: float = 10.0
    max_distance_correction_mm: float = 20.0
    max_z_correction_mm: float = 20.0


@dataclass(frozen=True)
class RobotConfig:
    robot_id: str
    topic: str

    @property
    def heartbeat_topic(self) -> str:
        return f"{self.topic.rstrip('/')}/HEARTBEAT"

    @property
    def command_topic(self) -> str:
        return f"{self.topic.rstrip('/')}/test"


@dataclass(frozen=True)
class ModelRoute:
    model_id: str
    kind: str
    service_id: str
    default: bool = False


@dataclass(frozen=True)
class AppConfig:
    mqtt: MQTTConfig
    coordinator: CoordinatorConfig
    robots: tuple[RobotConfig, ...]
    models: tuple[ModelRoute, ...]

    def robot(self, robot_id: str) -> RobotConfig:
        for robot in self.robots:
            if robot.robot_id == robot_id:
                return robot
        raise ConfigError(f"unknown robot_id: {robot_id}")

    def route(self, model_id: str, *, kind: str | None = None) -> ModelRoute:
        for route in self.models:
            if route.model_id == model_id and (kind is None or route.kind == kind):
                return route
        raise ConfigError(f"unknown {kind or 'model'}: {model_id}")

    def default_model(self, kind: str) -> ModelRoute:
        matches = [route for route in self.models if route.kind == kind]
        for route in matches:
            if route.default:
                return route
        if matches:
            return matches[0]
        raise ConfigError(f"no configured model for kind: {kind}")


def _table(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"missing [{key}] table")
    return value


def _optional_path(value: Any, base_dir: Path) -> Path | None:
    if value in (None, ""):
        return None
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else (base_dir / path).resolve()


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    mqtt_raw = _table(raw, "mqtt")
    coordinator_raw = _table(raw, "coordinator")

    host = str(mqtt_raw.get("host") or "").strip()
    if not host:
        raise ConfigError("mqtt.host is required")
    data_dir = _optional_path(coordinator_raw.get("data_dir", "../data"), config_path.parent)
    assert data_dir is not None

    mqtt = MQTTConfig(
        host=host,
        port=int(mqtt_raw.get("port", 8883)),
        keepalive=int(mqtt_raw.get("keepalive", 60)),
        topic_root=str(mqtt_raw.get("topic_root", "desk_buddy")).strip().strip("/"),
        client_id=str(mqtt_raw.get("client_id", "desk-buddy-vision-coordinator")),
        username_env=str(mqtt_raw.get("username_env", "DESK_BUDDY_MQTT_USERNAME")),
        password_env=str(mqtt_raw.get("password_env", "DESK_BUDDY_MQTT_PASSWORD")),
        tls=bool(mqtt_raw.get("tls", True)),
        ca_file=_optional_path(mqtt_raw.get("ca_file"), config_path.parent),
        cert_file=_optional_path(mqtt_raw.get("cert_file"), config_path.parent),
        key_file=_optional_path(mqtt_raw.get("key_file"), config_path.parent),
    )
    coordinator = CoordinatorConfig(
        service_id=str(coordinator_raw.get("service_id", "vision-coordinator")),
        data_dir=data_dir,
        command_timeout_seconds=float(coordinator_raw.get("command_timeout_seconds", 30.0)),
        worker_timeout_seconds=float(coordinator_raw.get("worker_timeout_seconds", 120.0)),
        artifact_chunk_size=int(coordinator_raw.get("artifact_chunk_size", 4096)),
        allow_extrapolated_motion=bool(coordinator_raw.get("allow_extrapolated_motion", False)),
        max_rotation_deg=float(coordinator_raw.get("max_rotation_deg", 45.0)),
        min_distance_mm=float(coordinator_raw.get("min_distance_mm", 0.0)),
        max_distance_mm=float(coordinator_raw.get("max_distance_mm", 180.0)),
        min_z_height_mm=float(coordinator_raw.get("min_z_height_mm", -30.0)),
        max_z_height_mm=float(coordinator_raw.get("max_z_height_mm", 120.0)),
        max_rotation_correction_deg=float(coordinator_raw.get("max_rotation_correction_deg", 10.0)),
        max_distance_correction_mm=float(coordinator_raw.get("max_distance_correction_mm", 20.0)),
        max_z_correction_mm=float(coordinator_raw.get("max_z_correction_mm", 20.0)),
    )

    robot_rows = raw.get("robots", [])
    if not isinstance(robot_rows, list) or not robot_rows:
        raise ConfigError("at least one [[robots]] entry is required")
    robots = tuple(
        RobotConfig(robot_id=str(row["robot_id"]), topic=str(row["topic"]).rstrip("/"))
        for row in robot_rows
        if isinstance(row, dict)
    )
    if len({robot.robot_id for robot in robots}) != len(robots):
        raise ConfigError("robot_id values must be unique")

    model_table = raw.get("models", {})
    if not isinstance(model_table, dict):
        raise ConfigError("[models] must be a table")
    models = tuple(
        ModelRoute(
            model_id=str(model_id),
            kind=str(values["kind"]),
            service_id=str(values["service_id"]),
            default=bool(values.get("default", False)),
        )
        for model_id, values in model_table.items()
        if isinstance(values, dict)
    )
    for kind in {route.kind for route in models}:
        if sum(1 for route in models if route.kind == kind and route.default) > 1:
            raise ConfigError(f"only one default model is allowed for kind {kind}")
    return AppConfig(mqtt=mqtt, coordinator=coordinator, robots=robots, models=models)

