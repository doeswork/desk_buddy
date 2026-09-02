"""Networking services. No Qt in here.

    broker_finder.py     is Mosquitto installed, is it running, what next?
    broker_commands.py   start / stop / restart the broker Studio owns
    accounts.py          who may connect, and which topics they may use

These are the objects the UI asks questions of. They return data and finished
strings; the pages under ui/pages/network/ decide only how that looks. Keeping
Qt out means they are testable without a window and reusable from a CLI or a
future headless mode.

Coming with the later steps in mosquitto_plan: account creation (step 3).
"""

from __future__ import annotations

from .accounts import Account, NewAccount
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

__all__ = [
    "Account",
    "BrokerReport",
    "NewAccount",
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
]
