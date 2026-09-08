"""The small, explicit preferences dialog reached from File."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ...models.config.prefrences import Preferences


class PreferencesDialog(QDialog):
    """Edits background-behavior choices without exposing storage details."""

    def __init__(self, model: Preferences, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setModal(True)
        self._model = model

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Studio no longer starts a broker, so this only says whether it
        # should connect to the machine's one at launch. The wording is the
        # honest version of the old "start the broker" checkbox.
        self.auto_start_broker = QCheckBox("Connect to the MQTT broker when Studio opens")
        self.auto_start_broker.setChecked(model.mqtt_broker_auto_start)
        self.auto_start_broker.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.auto_start_broker)

        detail = QLabel(
            "Uses this machine's Mosquitto. Studio never starts or stops it — "
            "see Network → Broker."
        )
        detail.setObjectName("CardBody")
        detail.setWordWrap(True)
        layout.addWidget(detail)

        buttons = QHBoxLayout()
        save = QPushButton("Save")
        save.setObjectName("ToolbarPrimary")
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self.save)
        buttons.addWidget(save)

        cancel = QPushButton("Cancel")
        cancel.setObjectName("ToolbarAction")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def save(self) -> None:
        self._model.set_mqtt_broker_auto_start(self.auto_start_broker.isChecked())
        self.accept()
