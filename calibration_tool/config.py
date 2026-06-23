from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Dict


TOOL_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TOOL_DIR.parent
ENV_PATH = TOOL_DIR / ".env"
DEFAULT_CA_CERT = TOOL_DIR / "mqtt-ca.crt"


def _parse_bool(value: str, default: bool) -> bool:
    if value == "":
        return default
    return value.lower() not in ("0", "false", "no", "off")


def _read_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def _quote_env(value: object) -> str:
    text = "" if value is None else str(value)
    if not text:
        return '""'
    if any(ch.isspace() for ch in text) or any(ch in text for ch in ['"', "'", "#", "="]):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


@dataclass
class WizardConfig:
    broker: str = "mqtt.deskbuddy.ai"
    port: int = 8883
    admin_user: str = ""
    admin_password: str = ""
    robot_topic: str = "esp32_5"
    client_id: str = "desk-buddy-calibration-wizard"
    sender: str = "calibration_wizard"
    tls: bool = True
    tls_insecure: bool = False
    ca_cert_path: str = str(DEFAULT_CA_CERT)
    timeout: float = 240.0

    @property
    def command_topic(self) -> str:
        return f"{self.robot_topic.strip().strip('/')}/test"

    @property
    def heartbeat_topic(self) -> str:
        return f"{self.robot_topic.strip().strip('/')}/HEARTBEAT"


def load_config() -> WizardConfig:
    values = _read_env_file(ENV_PATH)
    if not values:
        # Helpful migration path from the existing CLI config, without writing it.
        values = _read_env_file(PROJECT_DIR / ".env")

    def get(key: str, default: str = "") -> str:
        return os.environ.get(key, values.get(key, default))

    robot_topic = get("DESK_BUDDY_MQTT_ROBOT_TOPIC")
    command_topic = get("DESK_BUDDY_MQTT_COMMAND_TOPIC")
    if not robot_topic and command_topic and "/" in command_topic:
        robot_topic = command_topic.split("/", 1)[0]

    return WizardConfig(
        broker=get("DESK_BUDDY_MQTT_BROKER", "mqtt.deskbuddy.ai"),
        port=int(get("DESK_BUDDY_MQTT_PORT", "8883") or "8883"),
        admin_user=get("DESK_BUDDY_MQTT_ADMIN_USER"),
        admin_password=get("DESK_BUDDY_MQTT_ADMIN_PASSWORD"),
        robot_topic=robot_topic or "esp32_5",
        client_id=get("DESK_BUDDY_MQTT_CLIENT_ID", "desk-buddy-calibration-wizard"),
        sender=get("DESK_BUDDY_MQTT_SENDER", "calibration_wizard"),
        tls=_parse_bool(get("DESK_BUDDY_MQTT_TLS", "1"), True),
        tls_insecure=_parse_bool(get("DESK_BUDDY_MQTT_TLS_INSECURE", "0"), False),
        ca_cert_path=get("DESK_BUDDY_MQTT_CA_CERT", str(DEFAULT_CA_CERT)),
        timeout=float(get("DESK_BUDDY_MQTT_TIMEOUT", "240") or "240"),
    )


def save_config(config: WizardConfig, path: Path = ENV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Desk Buddy calibration wizard settings",
        f"DESK_BUDDY_MQTT_BROKER={_quote_env(config.broker)}",
        f"DESK_BUDDY_MQTT_PORT={_quote_env(config.port)}",
        f"DESK_BUDDY_MQTT_ADMIN_USER={_quote_env(config.admin_user)}",
        f"DESK_BUDDY_MQTT_ADMIN_PASSWORD={_quote_env(config.admin_password)}",
        f"DESK_BUDDY_MQTT_ROBOT_TOPIC={_quote_env(config.robot_topic)}",
        f"DESK_BUDDY_MQTT_COMMAND_TOPIC={_quote_env(config.command_topic)}",
        f"DESK_BUDDY_MQTT_HEARTBEAT_TOPIC={_quote_env(config.heartbeat_topic)}",
        f"DESK_BUDDY_MQTT_CLIENT_ID={_quote_env(config.client_id)}",
        f"DESK_BUDDY_MQTT_SENDER={_quote_env(config.sender)}",
        f"DESK_BUDDY_MQTT_TLS={_quote_env(1 if config.tls else 0)}",
        f"DESK_BUDDY_MQTT_TLS_INSECURE={_quote_env(1 if config.tls_insecure else 0)}",
        f"DESK_BUDDY_MQTT_CA_CERT={_quote_env(config.ca_cert_path)}",
        f"DESK_BUDDY_MQTT_TIMEOUT={_quote_env(config.timeout)}",
        "",
    ]
    path.write_text("\n".join(lines))
    try:
        path.chmod(0o600)
    except OSError:
        pass

