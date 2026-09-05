"""Compact, exclusive disclosure rows used by the Vision service dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...components import Column


@dataclass(frozen=True)
class DisclosureSpec:
    key: str
    title: str
    model: str
    setup: str
    process: str
    mqtt: str
    detail: QWidget
    primary: QPushButton | None = None


class DisclosureRow(QWidget):
    """One terse status row with details that do not exist visually until opened."""

    def __init__(self, spec: DisclosureSpec, *, expanded: bool = False) -> None:
        super().__init__()
        self.key = spec.key
        self.title = spec.title
        self.setObjectName("DisclosureRow")
        self.setProperty("sectionKey", spec.key)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        summary = QFrame()
        summary.setObjectName("DisclosureSummary")
        row = QHBoxLayout(summary)
        row.setContentsMargins(13, 9, 9, 9)
        row.setSpacing(12)

        title = QLabel(spec.title)
        title.setObjectName("ServiceName")
        title.setMinimumWidth(112)
        row.addWidget(title)
        row.addWidget(_value("Model", spec.model), 3)
        row.addWidget(_value("Setup", spec.setup), 3)
        row.addWidget(_value("Process", spec.process), 2)
        row.addWidget(_value("MQTT", spec.mqtt), 2)

        if spec.primary is not None:
            spec.primary.setObjectName("ServicePrimaryAction")
            spec.primary.setProperty("sectionKey", spec.key)
            row.addWidget(spec.primary)

        self.toggle = QPushButton()
        self.toggle.setObjectName("DisclosureToggle")
        self.toggle.setProperty("sectionKey", spec.key)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setAccessibleName(f"Show {spec.title} details")
        row.addWidget(self.toggle)
        layout.addWidget(summary)

        self.detail = spec.detail
        self.detail.setObjectName("DisclosureDetails")
        self.detail.setProperty("sectionKey", spec.key)
        layout.addWidget(self.detail)
        self.set_expanded(expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.toggle.blockSignals(True)
        self.toggle.setChecked(expanded)
        self.toggle.blockSignals(False)
        self.toggle.setText("Hide ⌄" if expanded else "Details ›")
        self.toggle.setAccessibleName(
            f"Hide {self.title} details" if expanded else f"Show {self.title} details"
        )
        self.detail.setHidden(not expanded)


class DisclosureAccordion(Column):
    """A group of rows where at most one detail panel is open."""

    def __init__(
        self,
        specs: tuple[DisclosureSpec, ...],
        *,
        expanded_key: str = "",
        expanded_changed: Callable[[str], None] | None = None,
    ) -> None:
        self.rows = {
            spec.key: DisclosureRow(spec, expanded=spec.key == expanded_key)
            for spec in specs
        }
        super().__init__(*self.rows.values(), spacing=8)
        self.setObjectName("VisionServicesDashboard")
        self.expanded_key = expanded_key if expanded_key in self.rows else ""
        self.expanded_changed = expanded_changed
        for key, row in self.rows.items():
            row.toggle.toggled.connect(
                lambda checked, value=key: self._toggle(value, checked)
            )

    def _toggle(self, key: str, checked: bool) -> None:
        selected = key if checked else ""
        self.expanded_key = selected
        for row_key, row in self.rows.items():
            row.set_expanded(row_key == selected)
        if self.expanded_changed is not None:
            self.expanded_changed(selected)


def _value(label: str, value: str) -> QWidget:
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(1)
    caption = QLabel(label)
    caption.setObjectName("ServiceFieldLabel")
    text = QLabel(value)
    text.setObjectName("ServiceFieldValue")
    text.setWordWrap(True)
    layout.addWidget(caption)
    layout.addWidget(text)
    return holder
