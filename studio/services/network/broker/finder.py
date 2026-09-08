"""Find Mosquitto: is it on this machine, is it running, and what next?

A service, not a widget. It answers in plain data and finished strings so the
Network page can render without deciding anything — no branching on `partial`
in a view, no assembling sentences from fields. Ask it a question, show what it
says.

Detection only. This module never installs anything and never asks for root —
it looks, reports, and hands back a command the user can read before running.
An app that runs a package manager as root unprompted is exactly the habit a
beginner should not be taught.

Studio needs two binaries, and they can be present independently:

    mosquitto         the broker itself
    mosquitto_passwd  creates the user accounts (step 3)

Some distributions split these into separate packages, so "the broker is
installed" is not the same question as "we can add a robot account".
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

BROKER = "mosquitto"
PASSWD_TOOL = "mosquitto_passwd"

# Places a package manager may put these that are not always on PATH — a GUI
# app inherits the desktop session's PATH, which is often shorter than a
# shell's. Homebrew on Apple Silicon is the common miss.
EXTRA_PATHS = (
    "/opt/homebrew/sbin",           # macOS, Apple Silicon
    "/opt/homebrew/bin",
    "/usr/local/sbin",              # macOS, Intel
    "/usr/local/bin",
    "/usr/sbin",                    # Linux, sbin is often absent for a GUI app
    "/sbin",
    r"C:\Program Files\mosquitto",  # Windows, the official installer's default
    r"C:\Program Files (x86)\mosquitto",
)

VERSION_PATTERN = re.compile(r"version\s+(\d+\.\d+(?:\.\d+)?)")

# How long to wait on `mosquitto -h`. It prints and exits immediately; anything
# slower is a broken binary or a stalled network mount, not something to hang
# the UI on.
PROBE_TIMEOUT = 5


@dataclass(frozen=True)
class Tool:
    """One binary we looked for."""

    name: str
    path: str = ""          # empty when not found
    version: str = ""       # empty when not found, or when it reports none

    @property
    def found(self) -> bool:
        return bool(self.path)


@dataclass(frozen=True)
class BrokerStatus:
    """What we know about Mosquitto on this machine."""

    broker: Tool
    passwd_tool: Tool

    @property
    def installed(self) -> bool:
        """True when we can both run a broker and create accounts for it."""
        return self.broker.found and self.passwd_tool.found

    @property
    def partial(self) -> bool:
        """The broker is here but its tooling is not, or the reverse.

        Worth its own state: "installed" would be a lie, and "missing" would
        send the user to install something they already have.
        """
        return self.broker.found != self.passwd_tool.found

    @property
    def summary(self) -> str:
        """The card's headline."""
        if self.installed:
            return f"Mosquitto {self.broker.version or 'installed'}"
        if self.broker.found:
            return f"Mosquitto {self.broker.version or ''} — {PASSWD_TOOL} missing".strip()
        if self.passwd_tool.found:
            return f"{PASSWD_TOOL} found, but the broker itself is missing"
        return "Mosquitto is not installed"

    @property
    def detail(self) -> str:
        """The card's body: which half is missing, and why it matters.

        Lives here rather than in the page because deciding *what is wrong* is
        this module's job — the view only decides how it looks.
        """
        if self.installed:
            return f"Installed at {self.broker.path}."
        if self.broker.found:
            return (
                "The broker is here, but mosquitto_passwd is not, and Studio "
                "needs it to create robot accounts. Install the full package:"
            )
        if self.passwd_tool.found:
            return (
                "The password tool is here, but the broker itself is not. "
                "Install the full package:"
            )
        return (
            "Studio runs its own private broker — no root, no system service, "
            "and nothing outside its own folder. Install Mosquitto to begin:"
        )


def find(name: str) -> str:
    """Absolute path to a binary, searching PATH and the usual install dirs."""
    found = shutil.which(name)
    if found:
        return found

    # shutil.which honours PATHEXT on Windows; do the same for the extra dirs.
    suffixes = [""]
    if os.name == "nt":
        suffixes = [ext.lower() for ext in os.environ.get("PATHEXT", ".EXE").split(";")]

    for directory in EXTRA_PATHS:
        for suffix in suffixes:
            candidate = Path(directory) / f"{name}{suffix}"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return ""


def version_of(path: str) -> str:
    """Ask the broker its version. Empty string if it will not say.

    `mosquitto -h` exits 0 and prints "mosquitto version 2.1.2" as its first
    stdout line. It also writes a "terminating" line to stderr, so read stdout
    specifically rather than merging the two.
    """
    if not path:
        return ""
    try:
        result = subprocess.run(
            [path, "-h"],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return ""

    match = VERSION_PATTERN.search(result.stdout)
    return match.group(1) if match else ""


def detect() -> BrokerStatus:
    """Look for both binaries. Never raises — a broken machine is a status."""
    broker_path = find(BROKER)
    return BrokerStatus(
        broker=Tool(BROKER, broker_path, version_of(broker_path)),
        # mosquitto_passwd reports no version of its own, so presence is all
        # we can honestly claim about it.
        passwd_tool=Tool(PASSWD_TOOL, find(PASSWD_TOOL)),
    )


# ---- Install advice -----------------------------------------------------
# Shown, never run. Ordered by how likely the user is to have that manager.

_LINUX_MANAGERS = (
    ("pacman", "sudo pacman -S mosquitto"),
    ("apt", "sudo apt install mosquitto mosquitto-clients"),
    ("apt-get", "sudo apt-get install mosquitto mosquitto-clients"),
    ("dnf", "sudo dnf install mosquitto"),
    ("yum", "sudo yum install mosquitto"),
    ("zypper", "sudo zypper install mosquitto"),
    ("apk", "sudo apk add mosquitto"),
)


# Where package managers live. Checked directly rather than trusting PATH:
# a GUI app inherits the desktop session's PATH, which frequently omits
# /usr/sbin — and bad install advice is worse than none.
_MANAGER_DIRS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin", "/usr/local/bin", "/opt/homebrew/bin")


def _has_manager(name: str) -> bool:
    if shutil.which(name):
        return True
    return any(Path(directory, name).is_file() for directory in _MANAGER_DIRS)


def install_command() -> str:
    """The command that would install Mosquitto here.

    Detects the package manager that is actually present rather than parsing
    the distro name — this machine reports ID=omarchy, which no hardcoded list
    would know, but it has pacman like any other Arch derivative.
    """
    system = platform.system()

    if system == "Darwin":
        if _has_manager("brew"):
            return "brew install mosquitto"
        return (
            'Install Homebrew from https://brew.sh, then run: brew install mosquitto'
        )

    if system == "Windows":
        return "Download the installer from https://mosquitto.org/download/"

    for manager, command in _LINUX_MANAGERS:
        if _has_manager(manager):
            return command

    return "Install the 'mosquitto' package with your system's package manager"


# ---- Is one actually running? -------------------------------------------

# Mosquitto's default, and the only port Studio looks on. Studio does not run
# a broker of its own — it uses the one the machine has — so there is no
# second port to reconcile against this one.
SYSTEM_PORT = 1883

# The systemd unit, named here so the wording lives in one place.
SERVICE_NAME = "mosquitto"

# A local TCP connect resolves in well under a millisecond, so this is cheap
# enough to call on every page build and on a timer.
PROBE_CONNECT_TIMEOUT = 0.15

# The one place this wording lives.
NO_BROKER_CHIP = "○ no broker"

# Addresses that mean "this machine, from this machine". Never handed to a
# robot: they would point it back at itself.
LOOPBACK_HOSTS = frozenset({"", "127.0.0.1", "localhost", "::1"})


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    """True when something is listening. Says nothing about what it is."""
    connection = socket.socket()
    connection.settimeout(PROBE_CONNECT_TIMEOUT)
    try:
        connection.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        connection.close()


def active_firewall() -> str:
    """The name of a running host firewall, or "" if none is detected.

    Only answers "is something filtering here". Whether a given port is
    allowed is `firewall_allows()`, which reads the rules.

    A local connect cannot answer either question: traffic to this machine's
    own address is routed over loopback and never traverses the INPUT chain,
    so `port_open()` reports success from here while an external device is
    refused. That gap is exactly the failure this warns about, and it is why
    the check is "is a firewall up", not "can I connect".
    """
    if platform.system() != "Linux":
        # Only Linux is handled: macOS's per-application firewall does not
        # block a listener the user started, and claiming to know about
        # Windows Firewall without checking would be a guess.
        return ""

    for service, name in (("ufw", "ufw"), ("firewalld", "firewalld")):
        try:
            result = subprocess.run(
                ["systemctl", "is-active", service],
                capture_output=True, text=True, timeout=PROBE_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.stdout.strip() == "active":
            return name
    return ""


def firewall_hint(port: int, firewall: str) -> str:
    """The command that would open `port` on `firewall`, for the user to read.

    Shown, never run — same rule as install_command(): a tool that edits
    firewall rules as root unprompted is not a habit to teach.
    """
    if firewall == "ufw":
        return f"sudo ufw allow from 192.168.0.0/16 to any port {port} proto tcp"
    if firewall == "firewalld":
        return (
            f"sudo firewall-cmd --permanent --add-port={port}/tcp "
            "&& sudo firewall-cmd --reload"
        )
    return ""


# ufw keeps its rules here, and — on every distribution that ships it —
# world-readable. `ufw status` refuses to run as anyone but root, which is
# what made this look unknowable, but the file it reads from is not secret.
UFW_RULES = Path("/etc/ufw/user.rules")
UFW_CONF = Path("/etc/ufw/ufw.conf")
UFW_DEFAULTS = Path("/etc/default/ufw")

# Each rule ufw holds is written above its iptables lines as a "tuple"
# comment, which is a far steadier thing to parse than the -A lines:
#
#     ### tuple ### allow tcp 1883 0.0.0.0/0 any 192.168.0.0/16 in
#                   |     |   |    |         |   |              |
#                   action|   port destination  source          direction
#                         protocol
TUPLE = re.compile(
    r"^### tuple ###\s+(?P<action>\S+)\s+(?P<proto>\S+)\s+(?P<port>\S+)\s+"
    r"(?P<dest>\S+)\s+\S+\s+(?P<source>\S+)\s+(?P<direction>\S+)"
)


@dataclass(frozen=True)
class FirewallVerdict:
    """Whether a port is reachable through the host firewall.

    Three outcomes, and the third is the honest one that keeps this useful:
    `known` is False when Studio could not read the rules at all, which is
    different from having read them and found nothing.
    """

    firewall: str = ""
    known: bool = False
    allowed: bool = False
    rule: str = ""

    @property
    def blocked(self) -> bool:
        """Read the rules, and the port is not among them."""
        return bool(self.firewall) and self.known and not self.allowed


def _ufw_enabled() -> bool:
    """ufw's own idea of whether it is on, from its config file."""
    try:
        return "ENABLED=yes" in UFW_CONF.read_text()
    except OSError:
        return False


def _ufw_default_is_deny() -> bool:
    """Whether unmatched inbound traffic is dropped.

    A firewall defaulting to ACCEPT blocks nothing, so an absent rule is
    not a problem worth reporting.
    """
    try:
        text = UFW_DEFAULTS.read_text()
    except OSError:
        # Assume the ufw default, which is DROP — the cautious reading, and
        # the one that matches a stock install.
        return True
    for line in text.splitlines():
        if line.strip().startswith("DEFAULT_INPUT_POLICY"):
            return "ACCEPT" not in line.upper()
    return True


def _port_in_range(port: int, spec: str) -> bool:
    """Whether `port` is covered by a ufw port field ("1883" or "1880:1890")."""
    if spec == "any":
        return True
    if ":" in spec:
        try:
            low, high = (int(part) for part in spec.split(":", 1))
        except ValueError:
            return False
        return low <= port <= high
    # A single rule can carry a comma-separated list of ports.
    for part in spec.split(","):
        try:
            if int(part) == port:
                return True
        except ValueError:
            continue
    return False


def _ufw_allows(port: int) -> FirewallVerdict:
    """Look for an inbound allow rule covering `port` on TCP."""
    try:
        lines = UFW_RULES.read_text().splitlines()
    except OSError:
        # Unreadable rather than absent: say so instead of claiming a block.
        return FirewallVerdict(firewall="ufw", known=False)

    if not _ufw_default_is_deny():
        return FirewallVerdict(
            firewall="ufw", known=True, allowed=True,
            rule="the default inbound policy is ACCEPT",
        )

    for line in lines:
        match = TUPLE.match(line.strip())
        if match is None:
            continue
        if match["direction"] != "in":
            continue
        if not match["action"].startswith("allow"):
            continue
        if match["proto"] not in ("tcp", "any"):
            continue
        if not _port_in_range(port, match["port"]):
            continue
        source = match["source"]
        where = "" if source in ("0.0.0.0/0", "any", "::/0") else f" from {source}"
        return FirewallVerdict(
            firewall="ufw", known=True, allowed=True,
            rule=f"allow {match['proto']} {match['port']}{where}",
        )

    return FirewallVerdict(firewall="ufw", known=True, allowed=False)


def firewall_allows(port: int) -> FirewallVerdict:
    """Is `port` open through this machine's firewall? Read, never guessed.

    `ufw status` needs root, which is what made this look unanswerable — but
    the file it reads is world-readable on every distribution that ships
    ufw, and so is the default policy. Reading it directly turns "a firewall
    is running, this might be your problem" into a straight answer.

    firewalld is deliberately not parsed. Its state lives across zone XML,
    runtime-versus-permanent rules and interface assignments, and getting
    that subtly wrong would be worse than the honest "unknown" this returns —
    a false "allowed" sends the user hunting the wrong fault entirely.
    """
    firewall = active_firewall()
    if not firewall:
        return FirewallVerdict()
    if firewall == "ufw":
        if not _ufw_enabled():
            return FirewallVerdict(
                firewall="ufw", known=True, allowed=True,
                rule="ufw is not enabled",
            )
        return _ufw_allows(port)
    return FirewallVerdict(firewall=firewall, known=False)


def lan_address() -> str:
    """Best-guess LAN IP for this machine, or "" if none is reachable.

    A robot on the network cannot reach 127.0.0.1 on the Studio machine —
    this is the address to bind the broker to instead, when the user
    deliberately asks for that (see BrokerPage's Localhost/LAN toggle).

    Uses a UDP "connect", which never actually sends a packet — it only asks
    the OS routing table which local interface would be used to reach
    8.8.8.8, and reads back the address that answer picked. That is more
    reliable than resolving the machine's own hostname (`gethostbyname_ex`),
    which can return a VPN or container interface instead of the real LAN
    one on a machine with several.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()


# ---- What the UI asks for -----------------------------------------------
# One call, everything the Network page needs, already in words. The page
# renders these; it does not decide them.


@dataclass(frozen=True)
class BrokerReport:
    """Detection and liveness together — the whole answer, in strings."""

    status: BrokerStatus
    port: int               # 0 when nothing is listening

    @property
    def running(self) -> bool:
        return bool(self.port)

    @property
    def installed(self) -> bool:
        return self.status.installed

    @property
    def headline(self) -> str:
        if self.running:
            return f"{self.status.summary} — running"
        return self.status.summary

    @property
    def detail(self) -> str:
        if self.running:
            return f"This machine's broker is listening on port {self.port}."
        if self.installed:
            return (
                f"{self.status.detail} Not running yet — start the "
                f"{SERVICE_NAME} service."
            )
        return self.status.detail

    @property
    def install_command(self) -> str:
        """Empty when nothing needs installing."""
        return "" if self.installed else install_command()

    @property
    def chip(self) -> str:
        """the workspace bar's always-visible broker indicator."""
        return chip_for(self.port)


def chip_for(port: int) -> str:
    """The chip's wording, in one place."""
    if not port:
        return NO_BROKER_CHIP
    return f"● broker on {port}"


def report() -> BrokerReport:
    """Everything the Network page needs, in one call.

    Costs a subprocess (`mosquitto -h`), so call it when the page is built or
    after something changed — not on a timer. For the workspace bar chip use
    `chip_text()`, which only needs to know whether a port answers.
    """
    from . import system

    broker = system.describe(connect=False)
    return BrokerReport(
        status=detect(), port=broker.port if broker.reachable else 0
    )


def chip_text() -> str:
    """the workspace bar's broker indicator, without the cost of full detection.

    A local socket check is ~0.05ms against ~2.25ms for report(), which
    matters when this runs every few seconds for the life of the app.
    """
    from . import system

    broker = system.describe(connect=False)
    return chip_for(broker.port if broker.reachable else 0)


def is_wsl() -> bool:
    return "microsoft" in platform.release().lower() or bool(os.getenv("WSL_DISTRO_NAME"))


def robot_endpoint() -> tuple[str, int]:
    """Where a robot should be pointed to reach the broker: (host, port).

    ("", 0) when there is nothing usable to hand over.

    One answer for both the Broker page's address card and the Serial
    Monitor's Set MQTT dialog, which were each deriving it and disagreeing.

    The host is always a LAN address, since loopback would point the robot
    back at itself.
    """
    from . import system

    from ....storage import keys
    from ....storage.settings import settings
    store = settings()
    if store.get(keys.SYSTEM_BROKER_SETUP_PAUSED) and not store.get(keys.SYSTEM_BROKER_NETWORK_READY):
        return "", 0
    advertised = store.get(keys.SYSTEM_BROKER_ROBOT_HOST)
    if advertised:
        if not store.get(keys.SYSTEM_BROKER_NETWORK_READY):
            return "", 0
        connection = store.get(keys.SYSTEM_BROKER_HOST) or "127.0.0.1"
        return (advertised, system.port()) if port_open(system.port(), connection) else ("", 0)
    # WSL's private NAT address is not a robot endpoint. Windows forwarding
    # is managed separately; enter its address when provisioning the robot.
    if is_wsl():
        return "", 0

    broker = system.describe(connect=False)
    if not broker.reachable:
        return "", 0
    host = broker.host
    if host in LOOPBACK_HOSTS:
        host = lan_address()
    return (host, broker.port) if host else ("", 0)


def studio_endpoint() -> tuple[str, int]:
    """Where *Studio* connects to the broker: (host, port), ("", 0) if none.

    Not the same question as `robot_endpoint()`. A robot needs a LAN address;
    Studio is on the same machine and may use loopback, which works even
    when the broker binds 127.0.0.1 only and has no LAN address to offer.
    """
    from . import system

    broker = system.describe(connect=False)
    if not broker.reachable:
        return "", 0
    # Prefer the recorded address, but fall back to loopback: a broker
    # bound only to 127.0.0.1 is still one Studio can use.
    if broker.host and port_open(broker.port, broker.host):
        return broker.host, broker.port
    return "127.0.0.1", broker.port


def studio_credentials() -> tuple[str, str]:
    """The username and password Studio authenticates with, for either mode."""
    from . import system

    return system.credentials()
