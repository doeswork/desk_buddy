"""Curated detector selection and managed-service controls."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...components import Card, Column
from ...pages.base import Page
from ...theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING


class ModelsPage(Page):
    key = "models"
    label = "Models"

    title = "Image Interpreter"
    subtitle = "Choose, install, and run one vetted zero-shot detector. Models are downloaded, never bundled."

    @property
    def status(self) -> str:
        state = self.workspace.manager.state
        if state.ready:
            return f"Detector ready on {state.device or 'unknown device'}"
        if state.operation:
            return state.progress_message or state.process_state.title()
        if state.error:
            return "Vision needs attention"
        return "No model running"

    def build_page(self) -> QWidget:
        state = self.workspace.manager.state
        sections = [ModelPicker(self.workspace)]
        if state.operation == "install":
            sections.append(InstallProgress(self.workspace))
        sections.append(ServiceState(self.workspace))
        if state.error:
            sections.append(Card("Could not finish that action", state.error))
        return Column(*sections)


class ModelPicker(QFrame):
    def __init__(self, workspace) -> None:
        super().__init__()
        self.setObjectName("Card")
        manager = workspace.manager
        selected = manager.selected
        state = manager.state

        layout = QVBoxLayout(self)
        layout.setContentsMargins(CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, CARD_MARGIN_V)
        layout.setSpacing(CARD_SPACING * 2)

        title = QLabel("Detection model")
        title.setObjectName("CardTitle")
        layout.addWidget(title)

        picker = QComboBox()
        picker.setObjectName("VisionModelPicker")
        for manifest in manager.catalog:
            suffix = " — Default" if manifest.model_id == manager.catalog[0].model_id else ""
            picker.addItem(manifest.name + suffix, manifest.model_id)
        picker.setCurrentIndex(max(0, picker.findData(selected.model_id)))
        picker.setEnabled(not state.operation)
        picker.currentIndexChanged.connect(
            lambda index: manager.select_model(str(picker.itemData(index))) if index >= 0 else None
        )
        layout.addWidget(picker)

        details = QLabel(
            f"{selected.provider}\n{selected.source}\n"
            f"Approximately {selected.size_mb} MB model download · {selected.license}\n"
            f"Pinned revision {selected.revision}"
        )
        details.setObjectName("CardBody")
        details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        details.setWordWrap(True)
        layout.addWidget(details)

        model_state = next(item for item in manager.models() if item.manifest.model_id == selected.model_id)
        status = "Running" if model_state.running else "Installed" if model_state.installed else "Not installed"
        if model_state.active and not model_state.running:
            status += " · active model"
        state_label = QLabel(status)
        state_label.setObjectName("VisionStateGood" if model_state.installed else "CardBody")
        layout.addWidget(state_label)

        row = QHBoxLayout()
        primary = QPushButton(workspace.primary_action_label)
        primary.setObjectName("ContextPrimary")
        primary.setCursor(Qt.PointingHandCursor)
        primary.setEnabled(workspace.primary_action is not None)
        if workspace.primary_action is not None:
            primary.clicked.connect(workspace.primary_action)
        row.addWidget(primary)

        auto = QCheckBox("Start with Studio")
        auto.setChecked(state.start_with_studio)
        auto.setEnabled(not state.operation)
        auto.toggled.connect(manager.set_start_with_studio)
        row.addWidget(auto)
        row.addStretch(1)
        layout.addLayout(row)


class InstallProgress(QFrame):
    def __init__(self, workspace) -> None:
        super().__init__()
        self.setObjectName("Card")
        state = workspace.manager.state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, CARD_MARGIN_V)
        layout.setSpacing(CARD_SPACING)
        title = QLabel("Installing detector")
        title.setObjectName("CardTitle")
        layout.addWidget(title)
        message = QLabel(state.progress_message or "Preparing installation…")
        message.setObjectName("CardBody")
        message.setWordWrap(True)
        layout.addWidget(message)
        progress = QProgressBar()
        progress.setObjectName("VisionProgress")
        if state.progress_total and state.progress_current is not None:
            progress.setRange(0, int(state.progress_total))
            progress.setValue(int(state.progress_current))
        else:
            progress.setRange(0, 0)
        layout.addWidget(progress)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("ContextAction")
        cancel.clicked.connect(workspace.manager.cancel_install)
        layout.addWidget(cancel, 0, Qt.AlignLeft)


class ServiceState(QFrame):
    def __init__(self, workspace) -> None:
        super().__init__()
        self.setObjectName("Card")
        manager = workspace.manager
        state = manager.state
        active = manager.active
        layout = QVBoxLayout(self)
        layout.setContentsMargins(CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, CARD_MARGIN_V)
        layout.setSpacing(CARD_SPACING)
        title = QLabel("Active service")
        title.setObjectName("CardTitle")
        layout.addWidget(title)
        if active is None:
            body = "No active detector yet. Choose a model above and select Install & Use."
        else:
            device = f" · {state.device}" if state.device else ""
            body = (
                f"{active.name}{device}\n"
                f"Process: {state.process_state} · MQTT: {state.mqtt_state}\n"
                f"{active.source}@{active.revision}"
            )
        label = QLabel(body)
        label.setObjectName("CardBody")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(label)
