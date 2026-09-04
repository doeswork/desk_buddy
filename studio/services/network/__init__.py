"""Networking services. No Qt in here.

    broker/finder.py     is Mosquitto installed, is it running, what next?
    broker/commands.py   start / stop / restart the broker Studio owns
    broker/accounts.py   applies account records to the broker's own files
    pub_sub/traffic.py   records every observed publication to SQLite

These are the objects the UI asks questions of. They return data and finished
strings; the pages under ui/workspaces/network/ decide only how that looks. Keeping
Qt out means they are testable without a window and reusable from a CLI or a
future headless mode.

Coming with the later steps in mosquitto_plan: account creation (step 3).
"""

from __future__ import annotations

from .broker import commands as broker_commands
from .broker.accounts import sync as sync_accounts
from .broker.commands import (
    CommandResult,
    broker_dir,
    config_path,
    is_ours,
    our_port,
    restart,
    shutdown as shutdown_broker,
    start,
    stop,
)
from .broker.finder import (
    BrokerReport,
    chip_text,
    BrokerStatus,
    Tool,
    detect,
    install_command,
    lan_address,
    port_open,
    report,
    running_port,
)
from .broker.topics import Topic, TOPICS
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
    "broker_commands",
    "sync_accounts",
    "CommandResult",
    "broker_dir",
    "config_path",
    "is_ours",
    "our_port",
    "restart",
    "shutdown_broker",
    "start",
    "stop",
    "chip_text",
    "BrokerStatus",
    "Tool",
    "detect",
    "install_command",
    "lan_address",
    "port_open",
    "report",
    "running_port",
    "Topic",
    "TOPICS",
    "TrafficRecorder",
    "MqttClient",
    "PublishResult",
    "calibration_messages",
    "mqtt_client",
    "publish_as",
]
