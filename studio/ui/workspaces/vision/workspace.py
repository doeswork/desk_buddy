"""Vision workspace owner; pages contain the presentation."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from ....services.vision import VisionServiceManager
from ...components import ActionSpec, Separator
from ..base import Workspace
from .detections import DetectionsPage
from .models import ModelsPage


class VisionWorkspace(Workspace):
    key = "vision"
    label = "Vision"
    page_classes = [ModelsPage, DetectionsPage]

    def __init__(self, *, manager: VisionServiceManager | None = None) -> None:
        self.manager = manager or VisionServiceManager()
        super().__init__()
        self.manager.changed.connect(self._manager_changed)

    @property
    def primary_action_label(self) -> str:
        state = self.manager.state
        selected = self.manager.selected
        if state.operation == "install":
            return "Cancel"
        if not self.manager.is_installed(selected.model_id):
            return "Install & Use"
        if state.ready and state.active_model_id == selected.model_id:
            return "Running"
        if state.active_model_id == selected.model_id:
            return "Start"
        return "Use & Start"

    @property
    def primary_action(self):
        state = self.manager.state
        selected = self.manager.selected
        if state.operation == "install":
            return self.manager.cancel_install
        if state.operation or state.pending_detection:
            return None
        if not self.manager.is_installed(selected.model_id):
            return self.manager.install_and_use
        if state.ready and state.active_model_id == selected.model_id:
            return None
        if state.active_model_id == selected.model_id:
            return self.manager.start
        return self.manager.use_and_start

    def build_actions(self) -> list:
        state = self.manager.state
        installed = self.manager.is_installed(self.manager.selected.model_id)
        can_remove = installed and not state.operation
        can_stop = state.process_state in {"starting", "running"}
        return [
            ActionSpec(
                self.primary_action_label,
                primary=True,
                on_click=self.primary_action,
            ),
            ActionSpec("Remove", on_click=self.confirm_remove if can_remove else None),
            Separator(),
            ActionSpec("Stop", on_click=self.manager.stop if can_stop else None),
            Separator(),
            ActionSpec("Test Photo", on_click=lambda: self.go_to("detections")),
        ]

    def confirm_remove(self) -> None:
        manifest = self.manager.selected
        answer = QMessageBox.question(
            self.widget(),
            f"Remove {manifest.name}?",
            f"Delete the downloaded {manifest.name} snapshot?\n\n"
            "The shared Vision runtime, photos, robots, and calibration data are kept.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.manager.remove_selected()

    def activate(self) -> None:
        self.manager.client.reconcile()
        self.refresh()

    def start_enabled(self) -> None:
        self.manager.start_enabled()

    @property
    def install_running(self) -> bool:
        return self.manager.state.operation == "install"

    def shutdown(self) -> None:
        self.manager.shutdown()

    def _manager_changed(self, _state) -> None:
        self.refresh()
