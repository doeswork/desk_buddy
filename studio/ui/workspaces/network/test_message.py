"""The Test Message page: publish one message as any account.

For provoking firmware and services on purpose — sending a malformed payload,
or seeing what a specific account's traffic looks like arriving in the debug
tray. It sends as the *account* picked, not as Studio: a real per-account
connection, authenticated with that account's own saved credentials, so this
is really that account on the wire. See
`studio.services.network.pub_sub.publish_as` — and its "success" only means
the broker took delivery of the publish, not that its ACL then let the
message onto the topic; MQTT gives a publisher no way to tell those apart, so
this page cannot claim to either. Watch the debug tray to see whether a
message actually landed.

Deliberately unguarded — any account, any topic, any body, including
"studio" itself. The one guardrail is a forced prefix on the message body, so
a test message is never mistaken for the real thing on the wire: whatever the
user types rides inside a `{"studio_test": true, "body": ...}` envelope
instead of going out bare.
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....models.config.mqtt_users import users
from ....services.network import PublishResult, publish_as
from ...components import Column
from ...pages.base import Page
from ...theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING

DEFAULT_BODY = '{"hello": "from studio"}'


def envelope(body: str) -> str:
    """Wrap the user's text so it always reads as a deliberate test message.

    The body is carried as a string rather than parsed and merged in: this
    page is also for sending intentionally malformed JSON, and reformatting
    the user's exact bytes would defeat that.
    """
    return json.dumps({"studio_test": True, "sender": "studio-test", "body": body})


class TestMessagePage(Page):
    key = "test_message"
    label = "Test Message"

    title = "Test Message"
    subtitle = "Publish one message as any user, to see what happens."

    def __init__(self, workspace=None) -> None:
        super().__init__(workspace)
        self._name = ""
        self._topic = ""
        self._body = DEFAULT_BODY
        self._result: PublishResult | None = None

    def build_page(self) -> QWidget:
        accounts = users().all()
        if not accounts:
            return Column(QLabel("No users yet. Create one on Users first."))

        if not self._name or self._name not in [a.name for a in accounts]:
            self._name = accounts[0].name
        if not self._topic:
            self._topic = f"{self._name}/test"

        return Column(SendForm(
            [account.name for account in accounts],
            self._name,
            self._topic,
            self._body,
            self._result,
            on_account_changed=self._account_changed,
            on_topic_changed=self._topic_changed,
            on_body_changed=self._body_changed,
            on_send=self.send,
        ))

    def _account_changed(self, name: str) -> None:
        self._name = name

    def _topic_changed(self, topic: str) -> None:
        self._topic = topic

    def _body_changed(self, body: str) -> None:
        self._body = body

    def send(self) -> None:
        account = users().find(self._name)
        if account is None:
            return
        self._result = publish_as(account, self._topic, envelope(self._body))
        self.rebuild()


class SendForm(QWidget):
    def __init__(self, names: list[str], name: str, topic: str, body: str,
                 result: PublishResult | None, *, on_account_changed,
                 on_topic_changed, on_body_changed, on_send) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, CARD_MARGIN_V
        )
        layout.setSpacing(CARD_SPACING * 2)

        form = QFormLayout()
        form.setSpacing(CARD_SPACING * 2)

        account = QComboBox()
        account.addItems(names)
        account.setCurrentText(name)
        account.currentTextChanged.connect(on_account_changed)
        form.addRow("Send as", account)

        topic_field = QLineEdit(topic)
        topic_field.textChanged.connect(on_topic_changed)
        form.addRow("Topic", topic_field)

        layout.addLayout(form)

        body_label = QLabel("Message body")
        body_label.setObjectName("CardTitle")
        layout.addWidget(body_label)

        body_field = QPlainTextEdit(body)
        body_field.setObjectName("CommandText")
        body_field.setFixedHeight(120)
        body_field.textChanged.connect(lambda: on_body_changed(body_field.toPlainText()))
        layout.addWidget(body_field)

        note = QLabel(
            "Sent inside a {\"studio_test\": true, \"body\": …} envelope, so a "
            "test message can never be mistaken for the real thing on the wire."
        )
        note.setObjectName("CardBody")
        note.setWordWrap(True)
        layout.addWidget(note)

        layout.addSpacing(CARD_SPACING * 2)
        row = QHBoxLayout()
        send = QPushButton("Send")
        send.setObjectName("ContextPrimary")
        send.setCursor(Qt.PointingHandCursor)
        send.clicked.connect(on_send)
        row.addWidget(send)
        row.addStretch(1)
        layout.addLayout(row)

        if result is not None:
            outcome = QLabel(result.message)
            outcome.setObjectName("CardBody" if result.ok else "FieldError")
            outcome.setWordWrap(True)
            layout.addWidget(outcome)
