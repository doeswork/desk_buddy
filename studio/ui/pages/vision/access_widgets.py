"""Shared managed Vision MQTT credential cards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from ....services.vision.access import ManagedCredentialState, VisionAccessIdentity
from ...components import Card, Column


@dataclass(frozen=True)
class AccessCallbacks:
    setup_all: Callable[[], None]
    ensure: Callable[[VisionAccessIdentity], None]
    rotate: Callable[[VisionAccessIdentity], None]
    revoke: Callable[[VisionAccessIdentity], None]
    copy_setup: Callable[[VisionAccessIdentity], None]


def vision_access_card(
    identities: Iterable[VisionAccessIdentity],
    states: Iterable[ManagedCredentialState],
    callbacks: AccessCallbacks,
    *,
    setup_label: str,
    setup_enabled: bool = True,
    detail: str = "Studio manages these least-privilege credentials for its localhost MQTT broker.",
) -> QWidget:
    identity_values = tuple(identities)
    state_by_id = {item.identity_id: item for item in states}
    card = Card("Vision MQTT Access", detail, muted=False)
    setup = QPushButton(setup_label)
    setup.setObjectName("VisionAccessSetupAll")
    setup.setEnabled(setup_enabled)
    setup.clicked.connect(callbacks.setup_all)
    card.layout().addWidget(setup)
    sections = [
        _identity_card(identity, state_by_id.get(identity.identity_id), callbacks)
        for identity in identity_values
    ]
    return Column(card, *sections)


def service_access_card(
    identity: VisionAccessIdentity,
    state: ManagedCredentialState,
    callbacks: AccessCallbacks,
) -> QWidget:
    return _identity_card(identity, state, callbacks, title="MQTT Access")


def _identity_card(
    identity: VisionAccessIdentity,
    state: ManagedCredentialState | None,
    callbacks: AccessCallbacks,
    *,
    title: str | None = None,
) -> QWidget:
    state = state or ManagedCredentialState(
        identity.identity_id, identity.role, identity.worker_id, identity.account_name,
        acl_summary=identity.acl_summary,
    )
    status = {
        "ready": "Ready",
        "missing": "Missing",
        "secret_missing": "Password missing",
        "acl_mismatch": "ACL mismatch",
        "error": "Error",
    }.get(state.status, state.status.title())
    card = Card(title or identity.role, state.error, muted=not state.ready)
    form = QFormLayout()
    form.addRow("Role", QLabel(identity.role))
    if identity.worker_id:
        form.addRow("Worker", QLabel(identity.worker_id))
    form.addRow("Status", QLabel(status))
    form.addRow("Account", QLabel(state.username or state.account_name))
    form.addRow("ACL", QLabel(state.acl_summary))
    form.addRow("Broker", QLabel(f"{state.host}:{state.port}"))
    form.addRow("Username", _copy_field(state.username))
    form.addRow("Password", _copy_field(state.password))
    card.layout().addLayout(form)

    row = QHBoxLayout()
    ensure = QPushButton("Create/Repair Access")
    ensure.setEnabled(not state.ready and state.can_manage)
    ensure.clicked.connect(lambda: callbacks.ensure(identity))
    row.addWidget(ensure)
    copy_setup = QPushButton("Copy Worker Setup" if identity.worker_id else "Copy Controller Setup")
    copy_setup.setEnabled(state.ready)
    copy_setup.clicked.connect(lambda: callbacks.copy_setup(identity))
    row.addWidget(copy_setup)
    rotate = QPushButton("Rotate")
    rotate.setEnabled(state.ready and state.can_manage)
    rotate.clicked.connect(lambda: callbacks.rotate(identity))
    row.addWidget(rotate)
    revoke = QPushButton("Revoke")
    revoke.setEnabled((state.ready or state.status == "secret_missing") and state.can_manage)
    revoke.clicked.connect(lambda: callbacks.revoke(identity))
    row.addWidget(revoke)
    row.addStretch(1)
    card.layout().addLayout(row)
    return card


def _copy_field(value: str) -> QWidget:
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    field = QLineEdit(value)
    field.setReadOnly(True)
    field.setObjectName("ManagedCredential")
    field.setPlaceholderText("Not created")
    row.addWidget(field, 1)
    copy = QPushButton("Copy")
    copy.setEnabled(bool(value))

    def copy_value() -> None:
        QGuiApplication.clipboard().setText(value)
        copy.setText("Copied")

    copy.clicked.connect(copy_value)
    row.addWidget(copy)
    return holder
