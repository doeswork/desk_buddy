"""Small Qt helpers shared by Vision page sections."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QDoubleSpinBox


class EventSink(QObject):
    def __init__(
        self,
        on_update: Callable[[str, dict], None],
        on_connection: Callable[[bool, str], None],
    ) -> None:
        super().__init__()
        self._on_update = on_update
        self._on_connection = on_connection

    @Slot(str, object)
    def update(self, kind: str, payload: object) -> None:
        self._on_update(kind, dict(payload) if isinstance(payload, dict) else {})

    @Slot(bool, str)
    def connection(self, connected: bool, message: str) -> None:
        self._on_connection(connected, message)


def number_input(minimum: float, maximum: float, value: float) -> QDoubleSpinBox:
    control = QDoubleSpinBox()
    control.setRange(minimum, maximum)
    control.setDecimals(2)
    control.setValue(min(maximum, max(minimum, value)))
    return control


def depth_crop_pixmap(values: tuple[float, ...]) -> QPixmap:
    pixels = bytes(max(0, min(255, round(float(value) * 255))) for value in values)
    image = QImage(pixels, 64, 64, 64, QImage.Format_Grayscale8).copy()
    return QPixmap.fromImage(image)
