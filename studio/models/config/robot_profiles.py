"""Saved connection profiles used to provision physical Desk Buddies.

Profiles are deliberately separate from MQTT accounts.  An account describes
what a broker accepts; a profile describes everything a particular board needs
to boot, including Wi-Fi and the transport mode.  Passwords are stored in the
same user-owned, mode-0600 record area as the existing MQTT account records.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

from ...storage.store import Store


@dataclass(frozen=True)
class RobotProfile:
    """One complete ESP32 connection profile."""

    profile_id: str
    name: str
    wifi_ssid: str = ""
    wifi_password: str = ""
    broker_server: str = ""
    broker_port: int = 1883
    mqtt_user: str = ""
    mqtt_password: str = ""
    client_id: str = ""
    tls: bool = False
    last_result: str = ""
    last_provisioned_at: str = ""
    broker_kind: str = "local"

    def to_json(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "wifi_ssid": self.wifi_ssid,
            "wifi_password": self.wifi_password,
            "broker_server": self.broker_server,
            "broker_port": self.broker_port,
            "mqtt_user": self.mqtt_user,
            "mqtt_password": self.mqtt_password,
            "client_id": self.client_id,
            "tls": self.tls,
            "last_result": self.last_result,
            "last_provisioned_at": self.last_provisioned_at,
            "broker_kind": self.broker_kind,
        }

    @classmethod
    def from_json(cls, raw) -> "RobotProfile | None":
        if not isinstance(raw, dict):
            return None
        profile_id = raw.get("profile_id")
        name = raw.get("name")
        if not isinstance(profile_id, str) or not profile_id:
            return None
        if not isinstance(name, str) or not name:
            return None

        def text(key: str, default: str = "") -> str:
            value = raw.get(key, default)
            return value if isinstance(value, str) else default

        port = raw.get("broker_port", 1883)
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = 1883
        if not 1 <= port <= 65535:
            port = 1883

        tls = raw.get("tls", False)
        if not isinstance(tls, bool):
            tls = str(tls).lower() in ("1", "true", "yes", "on")

        return cls(
            profile_id=profile_id,
            name=name,
            wifi_ssid=text("wifi_ssid"),
            wifi_password=text("wifi_password"),
            broker_server=text("broker_server"),
            broker_port=port,
            mqtt_user=text("mqtt_user"),
            mqtt_password=text("mqtt_password"),
            client_id=text("client_id"),
            tls=tls,
            broker_kind=(
                raw.get("broker_kind")
                if raw.get("broker_kind") in ("local", "custom")
                else "local"
            ),
            last_result=text("last_result"),
            last_provisioned_at=text("last_provisioned_at"),
        )


class RobotProfiles:
    """Named profiles backed by one atomic JSON record file."""

    def __init__(self, store: Store | None = None) -> None:
        self._store = store if store is not None else Store("robot_profiles")

    @property
    def path(self):
        return self._store.path

    def all(self) -> list[RobotProfile]:
        raw = self._store.read(default=[])
        if not isinstance(raw, list):
            return []
        found = [RobotProfile.from_json(entry) for entry in raw]
        return sorted(
            (profile for profile in found if profile is not None),
            key=lambda profile: profile.name.lower(),
        )

    def find(self, profile_id: str) -> RobotProfile | None:
        return next(
            (profile for profile in self.all() if profile.profile_id == profile_id),
            None,
        )

    def by_user(self, user: str) -> RobotProfile | None:
        return next(
            (profile for profile in self.all() if profile.mqtt_user == user),
            None,
        )

    def create(self, name: str, **fields) -> RobotProfile:
        profile = RobotProfile(
            profile_id=uuid.uuid4().hex,
            name=name.strip() or fields.get("mqtt_user", "Robot"),
            **fields,
        )
        self._save([*self.all(), profile])
        return profile

    def save(self, profile: RobotProfile) -> RobotProfile:
        entries = self.all()
        if any(entry.profile_id == profile.profile_id for entry in entries):
            entries = [
                profile if entry.profile_id == profile.profile_id else entry
                for entry in entries
            ]
        else:
            entries.append(profile)
        self._save(entries)
        return profile

    def update(self, profile_id: str, **fields) -> RobotProfile | None:
        profile = self.find(profile_id)
        if profile is None:
            return None
        return self.save(replace(profile, **fields))

    def remove(self, profile_id: str) -> None:
        self._save([
            profile for profile in self.all() if profile.profile_id != profile_id
        ])

    def _save(self, entries: list[RobotProfile]) -> None:
        self._store.write([profile.to_json() for profile in entries])


_instance: RobotProfiles | None = None


def profiles() -> RobotProfiles:
    global _instance
    if _instance is None:
        _instance = RobotProfiles()
    return _instance
