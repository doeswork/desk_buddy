"""Which robot the app is talking to, in one place.

Every workspace that sends a command needs the same answer to "which robot?",
and before this each one kept its own. That is not a tidiness problem: three
copies of the selection meant Manual Control could be pointed at one robot
while Calibration published to another, and a robot reflashed under a new MQTT
username left every copy pointing at a topic nothing subscribes to.

The selection is a name, not a record. `Robots` owns the records; holding one
here would mean a robot renamed or removed on Network -> Robots leaves a stale
copy behind, which is the bug this file exists to prevent. `name` resolves
through the registry on every read instead.

The choice persists: it is what the user picked, so it belongs in settings
beside the theme and the last workspace, and a restart should not silently
retarget the arm.
"""

from __future__ import annotations

from collections.abc import Callable

from ...storage import keys
from ...storage.settings import settings
from .robots import robots


class CurrentRobot:
    """The app-wide robot selection, and who to tell when it changes."""

    def __init__(self, store=None, registry: Callable[[], object] | None = None) -> None:
        self._settings = store if store is not None else settings()
        self._robots = registry if registry is not None else robots
        self._listeners: list[Callable[[str], None]] = []

    # ---- reading ----------------------------------------------------------
    @property
    def name(self) -> str:
        """The selected robot's account name, or "" when none is available.

        Falls back to the first marked robot rather than returning a name the
        registry no longer knows. A robot can be unmarked on Network -> Robots,
        or renamed by a reflash, while this selection sits in settings; a stale
        name would publish to a topic nothing is listening on and the command
        would vanish with no error anywhere.
        """
        registry = self._robots()
        stored = self._settings.get(keys.CURRENT_ROBOT)
        if stored and registry.find(stored) is not None:
            return stored
        available = registry.all()
        return available[0].name if available else ""

    @property
    def record(self):
        """The full record, for callers needing the label or more than a name."""
        name = self.name
        return self._robots().find(name) if name else None

    def available(self) -> list:
        return self._robots().all()

    # ---- writing ----------------------------------------------------------
    def select(self, name: str) -> None:
        """Point the app at a robot. No-ops when it is already the one."""
        if name == self.name:
            return
        self._settings.set(keys.CURRENT_ROBOT, name)
        for listener in tuple(self._listeners):
            listener(name)

    # ---- change notification ----------------------------------------------
    def watch(self, listener: Callable[[str], None]) -> Callable[[], None]:
        """Call `listener` on every change; returns an unsubscribe callable.

        Pages come and go as the user moves around, so the unsubscribe matters:
        a page that stays subscribed after being torn down is a callback into
        deleted Qt widgets.
        """
        self._listeners.append(listener)

        def unwatch() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unwatch


_instance: CurrentRobot | None = None


def current_robot() -> CurrentRobot:
    global _instance
    if _instance is None:
        _instance = CurrentRobot()
    return _instance
