"""Every MQTT publication Studio observes, persisted as raw bytes.

The database is the source of truth. The debug tray is only a view over these
records, so closing it or restarting Studio never loses traffic. Payloads stay
as bytes because MQTT does not require UTF-8 or JSON; formatting is best effort
and can never corrupt the stored message.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from .database import Database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _heartbeat(payload: bytes) -> bool:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and (
        value.get("log") == "heartbeat" or value.get("status") == "heartbeat"
    )


@dataclass(frozen=True)
class MqttMessage:
    id: int
    received_at: str
    topic: str
    payload: bytes
    qos: int = 0
    retained: bool = False
    heartbeat: bool = False

    @property
    def payload_text(self) -> str:
        """Readable one-line content without assuming messages are JSON."""
        try:
            text = self.payload.decode("utf-8")
        except UnicodeDecodeError:
            preview = self.payload[:32].hex(" ")
            suffix = " …" if len(self.payload) > 32 else ""
            return f"<{len(self.payload)} bytes: {preview}{suffix}>"

        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return text.replace("\r", "\\r").replace("\n", "\\n")
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @property
    def line(self) -> str:
        try:
            stamp = datetime.fromisoformat(self.received_at).astimezone()
            time_text = stamp.strftime("%H:%M:%S")
        except ValueError:
            time_text = self.received_at
        flags = []
        if self.qos:
            flags.append(f"qos={self.qos}")
        if self.retained:
            flags.append("retained")
        suffix = f" [{' '.join(flags)}]" if flags else ""
        return f"[{time_text}] {self.topic}{suffix}  {self.payload_text}"


class MqttMessages:
    """Append and query MQTT history without exposing SQL to callers."""

    def __init__(self, database: Database | None = None) -> None:
        self.database = database if database is not None else Database()

    @property
    def path(self):
        return self.database.path

    def append(
        self,
        topic: str,
        payload: bytes | bytearray | memoryview | str,
        *,
        qos: int = 0,
        retained: bool = False,
        received_at: str | None = None,
    ) -> MqttMessage:
        if isinstance(payload, str):
            raw = payload.encode()
        else:
            raw = bytes(payload)
        stamp = received_at or _now()
        heartbeat = _heartbeat(raw)

        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO mqtt_message
                    (received_at, topic, payload, qos, retained, heartbeat)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (stamp, topic, raw, qos, int(retained), int(heartbeat)),
            )
            message_id = int(cursor.lastrowid)
        return MqttMessage(
            message_id, stamp, topic, raw, qos, retained, heartbeat
        )

    def recent(
        self, limit: int = 1000, *, include_heartbeats: bool = False
    ) -> list[MqttMessage]:
        limit = max(0, int(limit))
        if not limit:
            return []
        where = "" if include_heartbeats else "WHERE heartbeat = 0"
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, received_at, topic, payload, qos, retained, heartbeat
                FROM mqtt_message
                {where}
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._from_row(row) for row in reversed(rows)]

    def count(self) -> int:
        with self.database.connect() as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM mqtt_message").fetchone()[0]
            )

    def latest_id(self) -> int:
        with self.database.connect() as connection:
            return int(
                connection.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM mqtt_message"
                ).fetchone()[0]
            )

    def clear(self) -> None:
        with self.database.connect() as connection:
            connection.execute("DELETE FROM mqtt_message")

    @staticmethod
    def _from_row(row) -> MqttMessage:
        return MqttMessage(
            id=row["id"],
            received_at=row["received_at"],
            topic=row["topic"],
            payload=bytes(row["payload"]),
            qos=row["qos"],
            retained=bool(row["retained"]),
            heartbeat=bool(row["heartbeat"]),
        )


_instance: MqttMessages | None = None


def mqtt_messages() -> MqttMessages:
    global _instance
    if _instance is None:
        _instance = MqttMessages()
    return _instance
