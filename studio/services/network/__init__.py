"""Networking services. No Qt in here.

    broker/finder.py     is Mosquitto installed, is it running, what next?
    broker/system.py     the machine's own broker: where it is, whether
                         Studio may edit its accounts, and what to run
    pub_sub/traffic.py   records every observed publication to SQLite

Studio does not run a broker. It uses the one the machine has — Mosquitto on
1883, installed and owned by the user — because a second broker beside a
working one is only a second place for a message to be, and one of them to be
wrong. What Studio cannot do without permission (editing /etc/mosquitto) it
asks for rather than assumes; see `broker/system.py`.

These are the objects the UI asks questions of. They return data and finished
strings; the pages under ui/workspaces/network/ decide only how that looks.
Keeping Qt out means they are testable without a window and reusable from a
CLI or a future headless mode.
"""

from __future__ import annotations

from .broker import system as broker_system
from .broker.finder import (
    SERVICE_NAME,
    SYSTEM_PORT,
    BrokerReport,
    BrokerStatus,
    Tool,
    FirewallVerdict,
    active_firewall,
    chip_text,
    detect,
    firewall_allows,
    firewall_hint,
    install_command,
    lan_address,
    port_open,
    report,
    robot_endpoint,
    studio_credentials,
    studio_endpoint,
)
from .broker.system import (
    WriteAccess,
    create_account,
    grant_instructions,
    remove_account,
    write_access,
)
from .broker.topics import TOPICS, Topic
from .pub_sub import (
    MqttClient,
    PublishResult,
    TrafficRecorder,
    calibration_messages,
    mqtt_client,
    publish_as,
)

__all__ = [
    "BrokerReport",
    "BrokerStatus",
    "SERVICE_NAME",
    "SYSTEM_PORT",
    "Tool",
    "FirewallVerdict",
    "active_firewall",
    "broker_system",
    "chip_text",
    "detect",
    "firewall_allows",
    "firewall_hint",
    "install_command",
    "lan_address",
    "port_open",
    "report",
    "robot_endpoint",
    "studio_credentials",
    "studio_endpoint",
    "WriteAccess",
    "create_account",
    "grant_instructions",
    "remove_account",
    "write_access",
    "Topic",
    "TOPICS",
    "TrafficRecorder",
    "MqttClient",
    "PublishResult",
    "calibration_messages",
    "mqtt_client",
    "publish_as",
]
