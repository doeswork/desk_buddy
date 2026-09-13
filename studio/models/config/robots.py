"""Which accounts are robots.

An account (`MqttUser`) is a credential — a robot has one, but so does a
vision server or a web app, and nothing about the credential says which.
A `Robot` is the other half: a record that says "this account is a physical
desk buddy," plus the friendly name the Calibration and Manual workspaces
show instead of the raw MQTT username.

The credential itself — password, topics — stays owned by `mqtt_users`. This
file only marks the account and never duplicates it, so there is exactly one
place a robot's connection details can drift from what the broker enforces.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...storage.store import Store
from .mqtt_users import STUDIO_NAME, Users, users


@dataclass(frozen=True)
class Robot:
    """One account, marked as a robot."""

    name: str
    label: str = ""

    @property
    def display_name(self) -> str:
        return self.label or self.name

    def to_json(self) -> dict:
        return {"name": self.name, "label": self.label}

    @classmethod
    def from_json(cls, raw) -> "Robot | None":
        if not isinstance(raw, dict):
            return None
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            return None
        label = raw.get("label")
        return cls(name=name, label=label if isinstance(label, str) else "")


class Robots:
    """Every robot Studio knows about, backed by one JSON file."""

    def __init__(self, store: Store | None = None, users_backend: Users | None = None) -> None:
        self._store = store if store is not None else Store("robots")
        if users_backend is not None:
            self._users = users_backend
        else:
            # Test and import callers can give Robots a throwaway Store. Use
            # the matching account file beside it instead of the app-wide
            # singleton; production's default Store still uses the singleton.
            directory = getattr(self._store, "_directory", None)
            self._users = (
                Users(Store("mqtt_users", directory=directory))
                if directory is not None else users()
            )

    @property
    def path(self):
        return self._store.path

    def all(self) -> list[Robot]:
        raw = self._store.read(default=[])
        if not isinstance(raw, list):
            return []
        found = [Robot.from_json(entry) for entry in raw]
        return sorted(
            (robot for robot in found if robot is not None),
            key=lambda robot: robot.name,
        )

    def find(self, name: str) -> Robot | None:
        return next((robot for robot in self.all() if robot.name == name), None)

    def add(self, name: str, label: str = "") -> tuple[Robot | None, str]:
        """Mark an existing account as a robot. Returns (robot, "") or (None, why not)."""
        if name == STUDIO_NAME:
            return None, "Studio's own account is not a robot."
        if self._users.find(name) is None:
            return None, (
                f"No account called {name!r}. Create it on Network → "
                "Accounts first, then mark it as a robot here."
            )
        if self.find(name) is not None:
            return None, f"{name!r} is already marked as a robot."

        robot = Robot(name=name, label=label.strip())
        self._save([*self.all(), robot])
        return robot, ""

    def upsert(self, name: str, label: str = "") -> tuple[Robot | None, str]:
        """Create a robot after its first heartbeat, or refresh its label."""
        if name == STUDIO_NAME:
            return None, "Studio's own account is not a robot."
        if self._users.find(name) is None:
            return None, f"No account called {name!r}."
        existing = self.find(name)
        updated = Robot(name=name, label=label.strip())
        if existing is None:
            self._save([*self.all(), updated])
        else:
            self._save([updated if robot.name == name else robot for robot in self.all()])
        return updated, ""

    def remove(self, name: str) -> str:
        """Unmark a robot. Empty string on success, else why not.

        This only forgets the marking — the underlying account and its
        calibration history are untouched, so re-adding the same name later
        picks both straight back up.
        """
        if self.find(name) is None:
            return f"{name!r} is not marked as a robot."
        self._save([robot for robot in self.all() if robot.name != name])
        return ""

    def _save(self, entries: list[Robot]) -> None:
        ordered = sorted(entries, key=lambda robot: robot.name)
        self._store.write([robot.to_json() for robot in ordered])


_instance: Robots | None = None


def robots() -> Robots:
    global _instance
    if _instance is None:
        _instance = Robots()
    return _instance
