"""User-selected Studio behavior that is neither data nor a UI concern.

The filename intentionally keeps the project's existing ``prefrences``
spelling. Models read and write named preferences; widgets only present them.
"""

from __future__ import annotations

from ...storage import keys
from ...storage.settings import Settings, settings


class Preferences:
    """The small set of choices that affect Studio's background behavior."""

    def __init__(self, backend: Settings | None = None) -> None:
        self._settings = backend if backend is not None else settings()

    @property
    def mqtt_broker_auto_start(self) -> bool:
        """Whether Studio starts its managed MQTT broker on launch."""
        return self._settings.get(keys.MQTT_BROKER_AUTO_START)

    def set_mqtt_broker_auto_start(self, enabled: bool) -> None:
        self._settings.set(keys.MQTT_BROKER_AUTO_START, bool(enabled))
        self._settings.sync()


_instance: Preferences | None = None


def preferences() -> Preferences:
    """The application-wide preferences model."""
    global _instance
    if _instance is None:
        _instance = Preferences()
    return _instance
