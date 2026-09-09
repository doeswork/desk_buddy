"""The fixed MQTT topic catalog.

Topics are a contract between Studio, firmware, and services — not a form the
user can accidentally change.  Account ACLs remain the place that decides who
may reach a topic.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ....services.network import TOPICS, Topic
from ...components import Card, Column
from ...pages.base import Page
from ...theme.metrics import CARD_MARGIN_H, CARD_MARGIN_V, CARD_SPACING


class TopicsPage(Page):
    key = "topics"
    label = "Topics"

    title = "Topics"
    subtitle = "The fixed message contracts Studio uses across the system."
    status = f"{len(TOPICS)} fixed channels"

    def build_page(self) -> QWidget:
        return Column(*(TopicCard(topic) for topic in TOPICS))


class TopicCard(Card):
    """A topic's immutable metadata and the workflows it carries."""

    def __init__(self, topic: Topic) -> None:
        super().__init__(topic.title, topic.description)

        layout = self.layout()
        layout.addSpacing(CARD_SPACING * 2)
        layout.addWidget(self._detail("Topic", topic.name, "CommandText"))
        layout.addWidget(self._detail("Publisher", topic.publisher))
        layout.addWidget(self._detail("Consumers", topic.consumers))

        heading = QLabel(topic.groups_title)
        heading.setObjectName("CardTitle")
        layout.addSpacing(CARD_SPACING * 2)
        layout.addWidget(heading)

        for group, actions in topic.message_groups:
            layout.addWidget(self._group(group, actions))

    @staticmethod
    def _detail(label: str, value: str, value_name: str = "CardBody") -> QWidget:
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(CARD_MARGIN_H, 0, CARD_MARGIN_H, 0)
        layout.setSpacing(0)

        key = QLabel(label.upper())
        key.setObjectName("CardBody")
        value_label = QLabel(value)
        value_label.setObjectName(value_name)
        value_label.setWordWrap(True)
        layout.addWidget(key)
        layout.addWidget(value_label)
        return row

    @staticmethod
    def _group(name: str, actions: tuple[str, ...]) -> QWidget:
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(CARD_MARGIN_H, CARD_MARGIN_V, CARD_MARGIN_H, 0)
        layout.setSpacing(0)

        title = QLabel(name)
        title.setObjectName("CardTitle")
        message_names = QLabel(", ".join(actions))
        message_names.setObjectName("CommandText")
        message_names.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(message_names)
        return row
