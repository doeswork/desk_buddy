"""Calibration — the workspace, and the robot selection its pages share.

Five steps, each a page: Base + Perch, IK, Visual, Reach and Grab, Stencil.
They run in order, and the side panel is the sequence, so where you are in
the process is where you are in the app.

The workspace owns what every step needs: which robot is being calibrated,
and the MQTT client they publish commands through. A step page reads both
from here rather than each holding its own connection — one shared client
is what lets a reply on one topic reach exactly the step waiting on it.
"""

from __future__ import annotations

from ....models.config.robots import robots
from ....services.network import mqtt_client
from ..base import Workspace
from .base_perch import BasePerchPage
from .ik import IKPage
from .reach_grab import ReachGrabPage
from .stencil import StencilPage
from .visual import VisualPage


class CalibrationWorkspace(Workspace):
    key = "calibration"
    label = "Calibration"

    page_classes = [
        BasePerchPage,
        IKPage,
        VisualPage,
        ReachGrabPage,
        StencilPage,
    ]

    def __init__(self) -> None:
        super().__init__()
        self._robot = ""

    # ---- robot selection ---------------------------------------------------
    def robots(self) -> list:
        return robots().all()

    @property
    def robot(self) -> str:
        """The account name of the robot being calibrated, or "" for none.

        Falls back to the first marked robot so a fresh session has something
        selected rather than an empty picker every step has to special-case.
        """
        if self._robot and robots().find(self._robot) is not None:
            return self._robot
        available = self.robots()
        return available[0].name if available else ""

    def select_robot(self, name: str) -> None:
        if name == self._robot:
            return
        self._robot = name
        self.refresh()

    # ---- the shared connection ---------------------------------------------
    def client(self):
        """Studio's shared command connection, connected to match the broker."""
        client = mqtt_client()
        client.reconcile()
        return client

    # ---- BAR 2 ---------------------------------------------------------------
    def build_actions(self) -> list:
        """Calibration has no workspace-wide buttons: every action is specific
        to the step's own form, and lives on that form instead — see
        AddAccountPage's Create/Cancel for the pattern this follows."""
        return []
