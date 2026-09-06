"""Every setting the app persists, declared once.

A key is a name, a type, and a default. Nothing else in the app may invent a
settings key inline: a typo in a raw string is a silently-lost preference that
nobody notices until a user reports "it forgets my theme", whereas a typo here
is an ImportError at startup.

The type matters more than it looks. QSettings stores values as text, and the
backends disagree about what comes back — a float written on Linux may return
as the string "1.3" on macOS. Declaring the type lets Settings.get() coerce on
read, so callers always get what they expect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Key:
    name: str          # the "section/key" path written to the file
    type: type         # what get() coerces to
    default: Any       # used on first run, and whenever the stored value is junk


# ---- Appearance ---------------------------------------------------------
THEME = Key("appearance/theme", str, "light")
ZOOM_INDEX = Key("appearance/zoom_index", int, 2)   # index into ZOOM_LEVELS

# ---- Window -------------------------------------------------------------
# Qt serialises these itself; we only carry the bytes.
GEOMETRY = Key("window/geometry", bytes, b"")
WINDOW_STATE = Key("window/state", bytes, b"")
# Where the user was: which workspace, and which of its pages.
LAST_WORKSPACE = Key("window/last_workspace", int, 0)
LAST_PAGE = Key("window/last_page", str, "")

# ---- Network ------------------------------------------------------------
MQTT_BROKER_AUTO_START = Key("network/mqtt_broker_auto_start", bool, True)

# Where the broker is. Not ours to choose — the user's mosquitto is wherever
# they configured it, so both are recorded rather than assumed.
SYSTEM_BROKER_HOST = Key("network/system_broker_host", str, "")
SYSTEM_BROKER_PORT = Key("network/system_broker_port", int, 1883)

# The account Studio itself uses on a system broker. Studio cannot write to
# /etc/mosquitto — that needs root — so it is told these rather than
# generating them, and the Broker page prints the commands to create them.
SYSTEM_BROKER_USER = Key("network/system_broker_user", str, "")
SYSTEM_BROKER_PASSWORD = Key("network/system_broker_password", str, "")

# Whether those credentials have actually connected. Studio cannot read
# /etc/mosquitto/passwd, so recording a password proves nothing about
# whether the account exists — only a successful CONNACK does.
SYSTEM_BROKER_VERIFIED = Key("network/system_broker_verified", bool, False)

# Whether Studio installed the polkit rule that lets it reload the broker
# without a password prompt on every account edit.
#
# Recorded here because it cannot be read back: /etc/polkit-1/rules.d is
# root:polkitd 0750 on a normal system, so an unprivileged process cannot
# even stat a file inside it — `Path.exists()` raises PermissionError rather
# than returning False. Checking the filesystem would therefore report "no
# rule" forever, including immediately after successfully writing one.
SYSTEM_BROKER_RELOAD_RULE = Key("network/system_broker_reload_rule", bool, False)

ALL = (
    THEME,
    ZOOM_INDEX,
    GEOMETRY,
    WINDOW_STATE,
    LAST_WORKSPACE,
    LAST_PAGE,
    MQTT_BROKER_AUTO_START,
    SYSTEM_BROKER_HOST,
    SYSTEM_BROKER_PORT,
    SYSTEM_BROKER_USER,
    SYSTEM_BROKER_PASSWORD,
    SYSTEM_BROKER_VERIFIED,
    SYSTEM_BROKER_RELOAD_RULE,
)
