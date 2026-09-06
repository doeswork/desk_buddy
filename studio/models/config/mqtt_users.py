"""Broker accounts, as records that outlive any one run of the app.

An account is not a robot. It is a credential — a username, a password, and
the topics it may reach. A desk buddy *has* one; so does a vision server, or a
web app, or anything else that joins the system.

**This file is the source of truth, not Mosquitto's.** The broker's passwd and
acl files are generated *from* these records by `studio.services.network`,
and are disposable: delete the broker directory and Studio rebuilds it on the
next start from what is here. That direction matters — it is what lets Studio
start a broker that already knows its accounts, rather than inventing a new
`studio` password on every launch because the only copy lived in a hash it
could not read back.

**Passwords are stored in the clear.** They are here so that a broker can be
rebuilt without changing them, and so a user can look one up again instead of
being handed a value once and told it is gone forever. The file is created
0600 in the user's own data directory. This is a deliberate trade for a tool
that manages a local hobby robot's broker, not a claim that plaintext
credentials are generally fine — see `security_plan` in PLAN.md before this
grows into anything multi-user.
"""

from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass, replace

from ...storage.store import Store
from .mqtt_topics import EVERYTHING, default_topics, validate_topic

# Usernames double as topic prefixes, so they are held to what makes a sane
# MQTT topic — not to what Mosquitto tolerates. Mosquitto only rejects a colon
# (its field separator), happily accepting "with space" and "üñî", both of
# which then turn every topic string, config line and firmware constant awkward.
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
NAME_RULE = (
    "2-32 characters: lowercase letters, digits, underscore or hyphen, "
    "starting with a letter or digit."
)

# Long enough that it never needs thinking about, and made of characters that
# survive a copy-paste into a firmware header or a shell command unquoted.
PASSWORD_ALPHABET = string.ascii_letters + string.digits
PASSWORD_LENGTH = 24

# Studio's own credential. Studio is a participant on its own bus like anything
# else — it publishes commands and reads telemetry — so it needs an account,
# and it needs the whole tree to do it. Created on demand rather than asked
# for: a broker with no way for Studio to reach it is not a working setup, and
# making the user create that by hand is asking them to perform a formality.
STUDIO_NAME = "studio"
STUDIO_DESCRIPTION = "Studio itself — created automatically"


def validate_name(name: str, taken: set[str] | frozenset[str] = frozenset()) -> str:
    """Empty string when the name is usable on a broker, else why it is not.

    A free function because the check is about MQTT and about a name, not
    about any particular list of accounts — the caller passes whichever
    names are already in use, which is now the broker's own set rather than
    a record file of Studio's.
    """
    if not name or not name.strip():
        return "A name is required."
    if name != name.strip():
        return "No leading or trailing spaces."
    if not NAME_PATTERN.match(name):
        return NAME_RULE
    if name in taken:
        return f"An account called {name!r} already exists."
    return ""


def generate_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))


@dataclass(frozen=True)
class MqttUser:
    """One broker account, as Studio records it."""

    name: str
    password: str = ""
    topics: tuple[str, ...] = ()

    @property
    def full_access(self) -> bool:
        return tuple(self.topics) == (EVERYTHING,)

    @property
    def access(self) -> str:
        """What this account may reach, in two words."""
        return "Full access" if self.full_access else "Own topics"

    @property
    def is_studio(self) -> bool:
        """Studio's own account, which the user may not remove."""
        return self.name == STUDIO_NAME

    @property
    def topics_display(self) -> str:
        """The topic list, comma-separated, for a table cell to show."""
        return ", ".join(self.topics) if self.topics else "(none)"

    @property
    def description(self) -> str:
        if self.is_studio:
            return STUDIO_DESCRIPTION
        if self.full_access:
            return "Full access to every topic"
        return f"Reaches: {self.topics_display}"

    # ---- serialisation ---------------------------------------------------
    def to_json(self) -> dict:
        return {
            "name": self.name,
            "password": self.password,
            "topics": list(self.topics),
        }

    @classmethod
    def from_json(cls, raw) -> "MqttUser | None":
        """One record, or None when the entry is not one.

        Tolerant on purpose: a hand-edited file with one bad entry should cost
        the user that entry, not every account they have.
        """
        if not isinstance(raw, dict):
            return None
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            return None

        password = raw.get("password")
        topics = raw.get("topics")
        return cls(
            name=name,
            password=password if isinstance(password, str) else "",
            topics=tuple(t for t in topics if isinstance(t, str))
            if isinstance(topics, list)
            else (),
        )


class Users:
    """Every account Studio knows about, backed by one JSON file."""

    def __init__(self, store: Store | None = None) -> None:
        self._store = store if store is not None else Store("mqtt_users")

    @property
    def path(self):
        return self._store.path

    # ---- reading ---------------------------------------------------------
    def all(self) -> list[MqttUser]:
        raw = self._store.read(default=[])
        if not isinstance(raw, list):
            return []
        found = [MqttUser.from_json(entry) for entry in raw]
        return sorted(
            (user for user in found if user is not None), key=lambda u: u.name
        )

    def find(self, name: str) -> MqttUser | None:
        return next((user for user in self.all() if user.name == name), None)

    def names(self) -> list[str]:
        return [user.name for user in self.all()]

    # ---- validation ------------------------------------------------------
    def validate(self, name: str) -> str:
        """Empty string when the name is usable, else why it is not."""
        if not name or not name.strip():
            return "A name is required."
        if name != name.strip():
            return "No leading or trailing spaces."
        if not NAME_PATTERN.match(name):
            return NAME_RULE
        if name == STUDIO_NAME:
            return f"{STUDIO_NAME!r} is reserved for Studio's own account."
        if name in self.names():
            return f"An account called {name!r} already exists."
        return ""

    # ---- writing ---------------------------------------------------------
    def add(self, name: str, *, full_access: bool = False) -> tuple[MqttUser | None, str]:
        """Record a new account. Returns (user, "") or (None, why not)."""
        problem = self.validate(name)
        if problem:
            return None, problem

        user = MqttUser(
            name=name,
            password=generate_password(),
            topics=default_topics(name, full_access=full_access),
        )
        self._save([*self.all(), user])
        return user, ""

    def ensure_studio(self) -> MqttUser:
        """Studio's own account, creating it the first time only.

        Idempotent, and — unlike the old broker-file-backed version — it keeps
        the password it made. That is the whole point of the record: a second
        launch finds this account already here and reuses it, instead of
        minting a new credential every time the app opens.
        """
        existing = self.find(STUDIO_NAME)
        if existing is not None:
            return existing

        user = MqttUser(
            name=STUDIO_NAME,
            password=generate_password(),
            topics=(EVERYTHING,),
        )
        self._save([*self.all(), user])
        return user

    def record(self, name: str, password: str) -> None:
        """Remember one account's password, replacing any earlier record.

        For accounts created against a broker Studio does not own the files
        of: the broker hashes the password and cannot give it back, so
        unless it is written down here at the moment it is generated, it is
        gone. Deliberately not `add()` — there is no name validation and no
        topic list, because the account already exists on the broker and
        this is only the secret catching up.
        """
        kept = [user for user in self.all() if user.name != name]
        self._save([*kept, MqttUser(name=name, password=password)])

    def forget(self, name: str) -> None:
        """Drop one account's record. Silent when there is nothing to drop.

        Unlike `remove()` this does not refuse Studio's own account: it is
        called when the broker has already deleted the account, and a record
        of a credential that no longer exists is worse than none.
        """
        self._save([user for user in self.all() if user.name != name])

    def reset_password(self, name: str) -> tuple[str, str]:
        """Give an account a new password. Returns (password, "")."""
        user = self.find(name)
        if user is None:
            return "", f"No account called {name!r}."

        password = generate_password()
        self._replace(replace(user, password=password))
        return password, ""

    def remove(self, name: str) -> str:
        """Forget an account. Empty string on success, else why not."""
        if name == STUDIO_NAME:
            # Removing it would cut Studio off from the broker it is managing,
            # and the next read of this list would recreate it anyway.
            return "Studio's own account cannot be removed."
        if self.find(name) is None:
            return f"No account called {name!r}."

        self._save([user for user in self.all() if user.name != name])
        return ""

    def add_topic(self, name: str, topic: str) -> tuple[tuple[str, ...] | None, str]:
        """Add one topic filter to an account. Returns (topics, "")."""
        user = self.find(name)
        if user is None:
            return None, f"No account called {name!r}."

        problem = validate_topic(topic, user.topics)
        if problem:
            return None, problem

        topics = (*user.topics, topic)
        self._replace(replace(user, topics=topics))
        return topics, ""

    def remove_topic(self, name: str, topic: str) -> tuple[tuple[str, ...] | None, str]:
        """Drop one topic filter from an account. Returns (topics, "").

        An account is allowed to end up with none: that is a real, if useless,
        state — connected but unable to publish or subscribe anywhere — and
        forcing one back in would be Studio overriding a deliberate choice.
        """
        user = self.find(name)
        if user is None:
            return None, f"No account called {name!r}."
        if topic not in user.topics:
            return None, f"{name!r} does not have the topic {topic!r}."

        topics = tuple(t for t in user.topics if t != topic)
        self._replace(replace(user, topics=topics))
        return topics, ""

    # ---- the file --------------------------------------------------------
    def _replace(self, user: MqttUser) -> None:
        self._save([user if u.name == user.name else u for u in self.all()])

    def _save(self, entries: list[MqttUser]) -> None:
        ordered = sorted(entries, key=lambda user: user.name)
        self._store.write([user.to_json() for user in ordered])


# The app-wide instance. Built lazily so importing this module never touches
# disk — tests construct their own Users(Store(..., directory=tmp)) instead.
_instance: Users | None = None


def users() -> Users:
    global _instance
    if _instance is None:
        _instance = Users()
    return _instance
