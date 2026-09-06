"""Network — the MQTT hub, and everything that talks through it.

    broker.py       is Mosquitto installed, and running?
    accounts.py     who may connect, and which topics they may use
    topics.py       fixed message contracts used by Studio
    add_account.py  the form behind Accounts' Add Account button

Coming with the later steps in mosquitto_plan: robot.py (heartbeat),
traffic.py (the live log).
"""

from __future__ import annotations

from .workspace import NetworkWorkspace

__all__ = ["NetworkWorkspace"]
