"""Networking services. No Qt in here.

    broker_finder.py     is Mosquitto installed, is it running, what next?
    broker_commands.py   start / stop / restart the broker Studio owns
    accounts.py          applies account records to the broker's own files

These are the objects the UI asks questions of. They return data and finished
strings; the pages under ui/workspaces/network/ decide only how that looks. Keeping
Qt out means they are testable without a window and reusable from a CLI or a
future headless mode.

Coming with the later steps in mosquitto_plan: account creation (step 3).
"""

from __future__ import annotations

from .accounts import sync as sync_accounts
from .broker_commands import (
    CommandResult,
    broker_dir,
    config_path,
    is_ours,
    restart,
    shutdown as shutdown_broker,
    start,
    stop,
)
from .broker_finder import (
    BrokerReport,
    chip_text,
    BrokerStatus,
    Tool,
    detect,
    install_command,
    port_open,
    report,
    running_port,
)
from .topics import Topic, TOPICS

__all__ = [
    "BrokerReport",
    "sync_accounts",
    "CommandResult",
    "broker_dir",
    "config_path",
    "is_ours",
    "restart",
    "shutdown_broker",
    "start",
    "stop",
    "chip_text",
    "BrokerStatus",
    "Tool",
    "detect",
    "install_command",
    "port_open",
    "report",
    "running_port",
    "Topic",
    "TOPICS",
]
