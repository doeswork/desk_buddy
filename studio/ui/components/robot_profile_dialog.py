"""The editable connection profile used by flashing and Network → Robots."""

from __future__ import annotations

import uuid

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...models.config.mqtt_users import STUDIO_NAME, generate_password, validate_name
from ...models.config.robot_profiles import RobotProfile
from ...services.firmware.commands import profile_provisioning_command


class RobotProfileDialog(QDialog):
    """Collect a complete profile, keeping secrets masked and off the log."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        profile: RobotProfile | None = None,
        profiles: list[RobotProfile] | None = None,
        accounts: list | None = None,
        default_server: str = "",
        default_port: int = 1883,
    ) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle("Robot connection profile")
        self._profiles = profiles or []
        self._accounts = [
            account for account in (accounts or [])
            if getattr(account, "name", "") != STUDIO_NAME
        ]
        self._account_names = {account.name for account in self._accounts}
        self._loading = False

        self.source = QComboBox()
        self.source.addItem("New local Mosquitto profile", "local-new")
        for entry in self._profiles:
            self.source.addItem(f"Saved: {entry.name}", entry.profile_id)
        self.source.addItem("Custom / cloud broker", "custom-new")
        self.source.currentIndexChanged.connect(self._source_changed)

        self.name = self._text("Profile name", 64)
        self.ssid = self._text("Wi-Fi network", 32)
        self.wifi_password = self._secret("Wi-Fi password", 64)
        self.server = self._text("Broker host", 128)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(default_port or 1883)
        self.account = QComboBox()
        self.account.setEditable(True)
        for entry in self._accounts:
            self.account.addItem(entry.name, entry.name)
        self.account.addItem("Create new robot account…", "__new__")
        self.account.currentIndexChanged.connect(self._account_changed)
        self.user = self._text("MQTT username", 64)
        self.mqtt_password = self._secret("MQTT password", 128)
        self.client_id = self._text("Client ID", 64)
        self.tls = QCheckBox("Use TLS")

        self.account_hint = QLabel(
            "Local robot accounts are restricted to the robot's own topic tree. "
            "Studio's account is never available here."
        )
        self.account_hint.setWordWrap(True)
        self.account_hint.setObjectName("CardBody")
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setObjectName("FieldError")
        self.error.hide()

        form = QFormLayout()
        form.addRow("Use profile", self.source)
        form.addRow("Name", self.name)
        form.addRow("Wi-Fi SSID", self.ssid)
        form.addRow("Wi-Fi password", self.wifi_password)
        form.addRow("MQTT broker", self.server)
        form.addRow("Port", self.port)
        form.addRow("Broker account", self.account)
        form.addRow("MQTT user", self.user)
        form.addRow("MQTT password", self.mqtt_password)
        form.addRow("Client ID", self.client_id)
        form.addRow("Transport", self.tls)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Save this profile before flashing. It remains available if upload or "
            "robot setup fails."
        ))
        layout.addLayout(form)
        layout.addWidget(self.account_hint)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

        initial = profile or self._new_profile(default_server, default_port)
        self._load(initial)

    @staticmethod
    def _text(placeholder: str, maximum: int) -> QLineEdit:
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        field.setMaxLength(maximum)
        return field

    @classmethod
    def _secret(cls, placeholder: str, maximum: int) -> QLineEdit:
        field = cls._text(placeholder, maximum)
        field.setEchoMode(QLineEdit.Password)
        return field

    def _new_profile(self, server: str, port: int) -> RobotProfile:
        user = "robot-1"
        existing = self._account_names | {entry.mqtt_user for entry in self._profiles}
        index = 1
        while user in existing:
            index += 1
            user = f"robot-{index}"
        return RobotProfile(
            profile_id=uuid.uuid4().hex,
            name=user,
            broker_server=server,
            broker_port=port or 1883,
            mqtt_user=user,
            mqtt_password=generate_password(),
            client_id=user,
            tls=False,
            broker_kind="local",
        )

    def _load(self, profile: RobotProfile) -> None:
        self._loading = True
        index = self.source.findData(profile.profile_id)
        if index < 0:
            index = self.source.findData("custom-new" if profile.broker_kind == "custom" else "local-new")
        self.source.setCurrentIndex(index)
        self.name.setText(profile.name)
        self.ssid.setText(profile.wifi_ssid)
        self.wifi_password.setText(profile.wifi_password)
        self.server.setText(profile.broker_server)
        self.port.setValue(profile.broker_port or 1883)
        account_index = self.account.findData(profile.mqtt_user)
        self.account.setCurrentIndex(
            account_index if account_index >= 0 else self.account.findData("__new__")
        )
        self.user.setText(profile.mqtt_user)
        self.mqtt_password.setText(profile.mqtt_password)
        self.client_id.setText(profile.client_id)
        self.tls.setChecked(profile.tls)
        self.tls.setEnabled(profile.broker_kind == "custom")
        self.account.setEnabled(profile.broker_kind == "local")
        self._loading = False

    def _account_changed(self, *_unused) -> None:
        if self._loading or self.is_custom:
            return
        selected = self.account.currentData()
        if selected == "__new__":
            if self.user.text().strip() in self._account_names or not self.user.text().strip():
                self.user.setText("robot-1")
            self.mqtt_password.setText(generate_password())
            return
        if selected:
            self.user.setText(str(selected))
            account = next((entry for entry in self._accounts if entry.name == selected), None)
            if account is not None and getattr(account, "password", ""):
                self.mqtt_password.setText(account.password)

    def _source_changed(self, *_unused) -> None:
        if self._loading:
            return
        data = self.source.currentData()
        if data in ("local-new", "custom-new"):
            if data == "local-new" and not self.mqtt_password.text():
                self.mqtt_password.setText(generate_password())
            self.tls.setEnabled(data == "custom-new")
            self.account.setEnabled(data == "local-new")
            return
        profile = next((entry for entry in self._profiles if entry.profile_id == data), None)
        if profile is not None:
            self._load(profile)

    @property
    def is_custom(self) -> bool:
        data = self.source.currentData()
        if data == "custom-new":
            return True
        selected = next((p for p in self._profiles if p.profile_id == data), None)
        return bool(selected and selected.broker_kind == "custom")

    @property
    def creates_account(self) -> bool:
        return not self.is_custom and (
            self.account.currentData() == "__new__"
            or self.user.text().strip() not in self._account_names
        )

    def profile(self) -> RobotProfile:
        selected = next(
            (entry for entry in self._profiles if entry.profile_id == self.source.currentData()),
            None,
        )
        return RobotProfile(
            profile_id=selected.profile_id if selected else uuid.uuid4().hex,
            name=self.name.text().strip() or self.user.text().strip(),
            wifi_ssid=self.ssid.text(),
            wifi_password=self.wifi_password.text(),
            broker_server=self.server.text().strip(),
            broker_port=self.port.value(),
            mqtt_user=self.user.text().strip(),
            mqtt_password=self.mqtt_password.text(),
            client_id=self.client_id.text().strip(),
            tls=self.tls.isChecked(),
            broker_kind="custom" if self.is_custom else "local",
            last_result=selected.last_result if selected else "",
            last_provisioned_at=selected.last_provisioned_at if selected else "",
        )

    def _accept(self) -> None:
        entry = self.profile()
        if not self.is_custom and entry.mqtt_user == STUDIO_NAME:
            self.error.setText("Studio's own account cannot be used for a robot.")
            self.error.show()
            return
        if not self.is_custom and self.creates_account:
            problem = validate_name(entry.mqtt_user, self._account_names)
            if problem:
                self.error.setText(problem)
                self.error.show()
                return
        try:
            profile_provisioning_command(
                wifi_ssid=entry.wifi_ssid,
                wifi_password=entry.wifi_password,
                server=entry.broker_server,
                port=entry.broker_port,
                user=entry.mqtt_user,
                password=entry.mqtt_password,
                client_id=entry.client_id,
                tls=entry.tls,
            )
        except ValueError as error:
            self.error.setText(str(error))
            self.error.show()
            return
        self.error.hide()
        self.accept()
