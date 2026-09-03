"""Broker accounts: who may connect, and which topics they may use.

An account is not a robot. It is a credential — a username, a password hash in
Mosquitto's file, and an ACL rule. A desk buddy *has* one; so does a vision
server, or a web app, or anything else that joins the system. That is the point
of putting everything on MQTT: a new participant needs an account, not another
protocol.

Two files, both ours, both regenerated from here:

    passwd   username:hash, managed by `mosquitto_passwd`
    acl      `user <name>` followed by its topic rules

Studio never asks the user to write either. Adding an account generates a strong
password, writes both files, and reloads the broker in place.

**Passwords cannot be read back.** Mosquitto stores a hash, so a password is
shown once at creation and is then unrecoverable — only resettable. Anything
here that returns a password is returning one it just generated.
"""

from __future__ import annotations

import re
import secrets
import signal
import string
import subprocess
from dataclasses import dataclass

from . import broker_commands as commands
from .broker_finder import PASSWD_TOOL, find

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

COMMAND_TIMEOUT = 10

# Studio's own credential. Studio is a participant on its own bus like anything
# else — it publishes commands and reads telemetry — so it needs an account,
# and it needs the whole tree to do it. Created on demand rather than asked
# for: a broker with no way for Studio to reach it is not a working setup, and
# making the user create that by hand is asking them to perform a formality.
STUDIO_NAME = "studio"
STUDIO_DESCRIPTION = "Studio itself — created automatically"


@dataclass(frozen=True)
class Account:
    """One broker account, as it exists on disk."""

    name: str
    full_access: bool = False

    @property
    def topics(self) -> str:
        """The topic tree this account may use."""
        return "#" if self.full_access else f"{self.name}/#"

    @property
    def access(self) -> str:
        """What this account may reach, in two words."""
        return "Full access" if self.full_access else "Own topics"

    @property
    def is_studio(self) -> bool:
        """Studio's own account, which the user may not remove."""
        return self.name == STUDIO_NAME

    @property
    def description(self) -> str:
        if self.is_studio:
            return STUDIO_DESCRIPTION
        if self.full_access:
            return "Full access to every topic"
        return f"Own topics only — {self.name}/#"


@dataclass(frozen=True)
class NewAccount:
    """A freshly created account, with the one and only copy of its password."""

    account: Account
    password: str


def generate_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))


def validate(name: str) -> str:
    """Empty string when the name is usable, else why it is not."""
    if not name or not name.strip():
        return "A name is required."
    if name != name.strip():
        return "No leading or trailing spaces."
    if not NAME_PATTERN.match(name):
        return NAME_RULE
    if name == STUDIO_NAME:
        return f"{STUDIO_NAME!r} is reserved for Studio's own account."
    if name in [account.name for account in accounts()]:
        return f"An account called {name!r} already exists."
    return ""


# ---- Reading what exists -------------------------------------------------

def accounts() -> list[Account]:
    """Every account, from the two files that define them.

    The passwd file is the source of truth for *existence* — an ACL entry with
    no password is unusable — and the ACL file says what each may reach.
    """
    passwd = commands.passwd_path()
    if not passwd.exists():
        return []

    full_access = _full_access_names()
    found = []
    for line in passwd.read_text().splitlines():
        name, separator, _ = line.partition(":")
        if separator and name:
            found.append(Account(name, full_access=name in full_access))
    return sorted(found, key=lambda account: account.name)


def _full_access_names() -> set[str]:
    """Accounts whose ACL grants the whole tree rather than their own prefix."""
    acl = commands.acl_path()
    if not acl.exists():
        return set()

    names, current = set(), ""
    for raw in acl.read_text().splitlines():
        line = raw.strip()
        if line.startswith("user "):
            current = line[5:].strip()
        elif line.startswith("topic ") and current:
            # "topic readwrite #" — the whole tree, not a prefix.
            if line.split()[-1] == "#":
                names.add(current)
    return names


# ---- Changing it ---------------------------------------------------------

def add(name: str, *, full_access: bool = False) -> tuple[NewAccount | None, str]:
    """Create an account. Returns (account, "") or (None, why not).

    The password is generated here and returned once. It is hashed into the
    passwd file and cannot be read back afterwards.
    """
    problem = validate(name)
    if problem:
        return None, problem

    tool = find(PASSWD_TOOL)
    if not tool:
        return None, f"{PASSWD_TOOL} is not installed, so accounts cannot be created."

    commands.write_config()      # ensures the directory and both files exist
    password = generate_password()

    result = _run([tool, "-b", str(commands.passwd_path()), name, password])
    if result is not None:
        return None, result

    account = Account(name, full_access=full_access)
    _write_acl([*accounts_without(name), account])
    reload_broker()
    return NewAccount(account, password), ""


def ensure_studio() -> tuple[NewAccount | None, str]:
    """Make sure Studio's own account exists. Returns (created, "").

    `created` is None when it was already there, which is the usual case —
    this runs on every visit to the accounts list, and doing nothing is the
    answer almost every time. The password is returned only on the call that
    creates it, since that is the only moment it can be known.

    Not routed through add(): validate() reserves the name precisely so a user
    cannot take it, and this is the one caller allowed to use it.
    """
    if any(account.is_studio for account in accounts()):
        return None, ""

    tool = find(PASSWD_TOOL)
    if not tool:
        return None, f"{PASSWD_TOOL} is not installed, so accounts cannot be created."

    commands.write_config()      # ensures the directory and both files exist
    password = generate_password()

    result = _run([tool, "-b", str(commands.passwd_path()), STUDIO_NAME, password])
    if result is not None:
        return None, result

    account = Account(STUDIO_NAME, full_access=True)
    _write_acl([*accounts_without(STUDIO_NAME), account])
    reload_broker()
    return NewAccount(account, password), ""


def reset_password(name: str) -> tuple[str, str]:
    """Generate a new password for an existing account. Returns (password, "").

    `mosquitto_passwd -b` on an existing user updates the hash in place rather
    than adding a second line, which is exactly a reset.
    """
    existing = {account.name: account for account in accounts()}
    if name not in existing:
        return "", f"No account called {name!r}."

    tool = find(PASSWD_TOOL)
    if not tool:
        return "", f"{PASSWD_TOOL} is not installed."

    password = generate_password()
    result = _run([tool, "-b", str(commands.passwd_path()), name, password])
    if result is not None:
        return "", result

    reload_broker()
    return password, ""


def remove(name: str) -> str:
    """Delete an account and its ACL. Empty string on success, else why not."""
    if name == STUDIO_NAME:
        # Removing it would cut Studio off from the broker it is managing, and
        # the next visit to the accounts list would recreate it anyway.
        return "Studio's own account cannot be removed."
    if name not in [account.name for account in accounts()]:
        return f"No account called {name!r}."

    tool = find(PASSWD_TOOL)
    if not tool:
        return f"{PASSWD_TOOL} is not installed."

    result = _run([tool, "-D", str(commands.passwd_path()), name])
    if result is not None:
        return result

    _write_acl(accounts_without(name))
    reload_broker()
    return ""


def accounts_without(name: str) -> list[Account]:
    return [account for account in accounts() if account.name != name]


# ---- The ACL file --------------------------------------------------------

ACL_HEADER = """\
# Generated by Desk Buddy Studio. Edits are overwritten when accounts change.
#
# Each account reaches its own topic tree by default, so one robot's traffic
# can never appear under another's. An account marked full-access sees the
# whole broker, which is what a vision server or a web app needs.
"""


def _write_acl(entries: list[Account]) -> None:
    lines = [ACL_HEADER]
    for account in sorted(entries, key=lambda item: item.name):
        lines.append(f"user {account.name}")
        lines.append(f"topic readwrite {account.topics}")
        lines.append("")

    path = commands.acl_path()
    path.write_text("\n".join(lines))
    path.chmod(0o600)


def reload_broker() -> bool:
    """Make a running broker re-read the account files, without dropping anyone.

    SIGHUP is verified to work: an account added to the files goes from refused
    to publishing with no restart and no dropped connections. Windows has no
    SIGHUP, so there the broker is restarted instead — connections drop, but
    correctness beats elegance and nothing is connected during setup anyway.
    """
    if not commands.is_ours():
        return False

    process = commands._process
    if process is None:
        return False

    if hasattr(signal, "SIGHUP"):
        process.send_signal(signal.SIGHUP)
        return True

    return bool(commands.restart())


def _run(argv: list[str]) -> str | None:
    """Run a mosquitto_passwd command. None on success, else the error text."""
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=COMMAND_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError) as error:
        return str(error)

    if result.returncode == 0:
        return None

    # mosquitto_passwd's own message beats anything generic we would write.
    message = (result.stderr or result.stdout).strip()
    return message or f"{argv[0]} failed with code {result.returncode}."
