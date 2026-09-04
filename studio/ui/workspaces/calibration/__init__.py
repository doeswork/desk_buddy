"""Calibration — Base + Perch, IK, Visual, Reach and Grab, Stencil.

One file per step, mirroring `ui/workspaces/network/`: `workspace.py` owns
what every step shares (the selected robot, the MQTT client), and each step
is its own page + form, following `network/add_account.py`'s shape.
"""

from __future__ import annotations

from .workspace import CalibrationWorkspace

__all__ = ["CalibrationWorkspace"]
