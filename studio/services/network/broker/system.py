"""System broker state, credentials and account management.

Network activation delegates installation and privileged configuration to the
asynchronous setup coordinator. This module supplies the existing account APIs
and manual fallback instructions; OS prompts own administrator authentication.
"""

from __future__ import annotations

import getpass
import os
import platform
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ....storage import keys
from ....storage.settings import Settings, settings
from .finder import (
    BROKER,
    PASSWD_TOOL,
    PROBE_TIMEOUT,
    SYSTEM_PORT,
    find,
    lan_address,
    port_open,
)

# How long any of the mosquitto CLI tools may take. They are local file
# edits; anything slower than this has hung.
COMMAND_TIMEOUT = 10

# How long to wait on the elevated grant. Much longer than COMMAND_TIMEOUT
# because the wait is a human reading a password prompt, not a file edit.
GRANT_TIMEOUT = 120

# Default account files shared with the authorized setup helper.
CONFIG_DIR = Path("/etc/mosquitto")
PASSWD_FILE = CONFIG_DIR / "passwd"
ACL_FILE = CONFIG_DIR / "acl"
CONFIG_FILE = CONFIG_DIR / "mosquitto.conf"

# The service name on every distribution that ships mosquitto as one.
SERVICE = "mosquitto"

# The group the broker runs as, and so the group that has to keep read
# access to the account files. Not assumed to exist — see `grant_access()`.
GROUP = "mosquitto"

# Reloading the broker is a privileged action, so polkit decides whether it
# needs a password. Without a rule the desktop asks *every* time — once per
# account edit — which is the single most irritating thing about managing
# users, and it teaches the user to click through prompts unread.
#
# This rule authorises exactly one verb on exactly one unit for exactly one
# user: reload mosquitto.service, for whoever installed it, from an active
# local session. It cannot start, stop, disable or touch any other service.
POLKIT_RULE = Path("/etc/polkit-1/rules.d/49-desk-buddy-mosquitto.rules")



@dataclass(frozen=True)
class SystemBroker:
    """Where the system broker is and whether Studio can use it yet.

    Deliberately not a live connection — this answers "what is the state of
    the setup", which the Broker page renders and `robot_endpoint()` reads.
    """

    host: str
    port: int
    reachable: bool
    user: str
    has_password: bool
    installed: bool
    service_active: bool
    verified: bool = False

    @property
    def recorded(self) -> bool:
        """Studio has credentials written down — not that they work."""
        return bool(self.host and self.port and self.user and self.has_password)

    @property
    def configured(self) -> bool:
        """Credentials are recorded *and* the broker has accepted them.

        Both halves matter. The account lives in /etc/mosquitto/passwd,
        which Studio cannot read, so having written a password down says
        nothing about whether the user ever ran the command that creates it.
        """
        return self.recorded and self.verified

    @property
    def usable(self) -> bool:
        return self.configured and self.reachable

    @property
    def headline(self) -> str:
        if not self.installed:
            return "Mosquitto is not installed"
        if not self.reachable:
            return "Broker not running"
        return f"Broker running on {self.host}:{self.port}"

    @property
    def detail(self) -> str:
        if not self.installed:
            return (
                "Studio uses the broker this machine runs. Install Mosquitto "
                "and start it, then reopen this page."
            )
        if not self.reachable:
            if not self.service_active:
                return (
                    f"Mosquitto is installed but the {SERVICE} service is not "
                    "running. Start it and reopen this page."
                )
            return (
                "Mosquitto is installed and its service is running, but "
                f"nothing answered on {self.host}:{self.port}. Check the "
                "listener in /etc/mosquitto/mosquitto.conf."
            )
        # Two separate facts, said separately: the broker is up (the
        # headline), and Studio is or is not talking to it. A running broker
        # that refuses Studio's account looks identical to a working one
        # unless the second line says otherwise.
        if not self.configured:
            return (
                "Studio is not connected — it needs an account on this "
                "broker to publish and subscribe. The commands below create "
                "one."
            )
        return (
            f"Connected as “{self.user}”. Robots reach it at "
            f"{self.host}:{self.port}."
        )


def _backend(backend: Settings | None = None) -> Settings:
    return backend if backend is not None else settings()


def host(backend: Settings | None = None) -> str:
    """The system broker's address.

    Empty setting means "this machine", which is resolved to its LAN address
    rather than loopback: the address is handed to robots, and a robot cannot
    reach 127.0.0.1 on someone else's machine.
    """
    return _backend(backend).get(keys.SYSTEM_BROKER_HOST) or lan_address()


def port(backend: Settings | None = None) -> int:
    return _backend(backend).get(keys.SYSTEM_BROKER_PORT) or SYSTEM_PORT


def credentials(backend: Settings | None = None) -> tuple[str, str]:
    """The account Studio uses on the system broker, as (user, password)."""
    store = _backend(backend)
    return (
        store.get(keys.SYSTEM_BROKER_USER),
        store.get(keys.SYSTEM_BROKER_PASSWORD),
    )


def set_connection(
    *,
    host_value: str = "",
    port_value: int = SYSTEM_PORT,
    user: str = "",
    password: str = "",
    backend: Settings | None = None,
) -> None:
    """Record where the system broker is and how to authenticate to it."""
    store = _backend(backend)
    store.set(keys.SYSTEM_BROKER_HOST, host_value.strip())
    store.set(keys.SYSTEM_BROKER_PORT, int(port_value) or SYSTEM_PORT)
    store.set(keys.SYSTEM_BROKER_USER, user.strip())
    store.set(keys.SYSTEM_BROKER_PASSWORD, password)
    store.set(keys.SYSTEM_BROKER_VERIFIED, False)
    store.set(keys.SYSTEM_BROKER_ROBOT_HOST, "")
    store.set(keys.SYSTEM_BROKER_NETWORK_READY, False)
    store.sync()


def service_active() -> bool:
    """Whether the mosquitto service is running, where that is knowable.

    Only systemd is checked. Elsewhere this returns False and callers fall
    back to the port probe, which is the fact that actually matters — a
    broker that answers is running whatever any service manager thinks.
    """
    if platform.system() != "Linux" or not shutil.which("systemctl"):
        return False
    try:
        result = subprocess.run(
            ["systemctl", "is-active", SERVICE],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout.strip() == "active"


def describe(backend: Settings | None = None, *, connect: bool = True) -> SystemBroker:
    """The system broker's state, in one call, for the page to render.

    Connects if it has credentials and has not already proved they work.
    Studio used to wait to be asked, which meant a correctly finished setup
    still displayed as unfinished until the user found a button and pressed
    it — the connection was the one fact the page could not tell them
    without being told to look. It costs about a second, once.

    `connect=False` for callers that only want the cheap facts (is it
    installed, is the port open) without a network round trip.
    """
    store = _backend(backend)
    address = host(store)
    number = port(store)
    user, password = credentials(store)

    # The port probe is the real test. It is tried on the recorded address
    # and on loopback: a broker bound to 0.0.0.0 answers both, while one
    # bound only to 127.0.0.1 answers the second and is still a broker
    # Studio can use — it just cannot be handed to a robot.
    reachable = port_open(number, address) if address else False
    if not reachable:
        reachable = port_open(number, "127.0.0.1")

    verified = bool(store.get(keys.SYSTEM_BROKER_VERIFIED))

    # Try the credentials rather than waiting to be asked. Only when there
    # is something to try and something to learn: a broker that is not
    # answering has nothing to authenticate against, and one that already
    # authenticated is not re-tested on every page build.
    if connect and reachable and user and password and not verified:
        verified = not verify(store)

    return SystemBroker(
        host=address,
        port=number,
        reachable=reachable,
        user=user,
        has_password=bool(password),
        installed=bool(find(BROKER)),
        service_active=service_active(),
        verified=verified,
    )


# ---- What the user has to run -------------------------------------------
# Manual fallback commands. Normal setup uses the authorized helper instead.


def install_instructions() -> list[tuple[str, str]]:
    """(caption, command) pairs to get a broker running on this machine."""
    system = platform.system()
    if system == "Darwin":
        return [
            ("Install Mosquitto", "brew install mosquitto"),
            ("Start it now and at login", "brew services start mosquitto"),
        ]
    if system == "Windows":
        return [
            (
                "Install Mosquitto",
                "winget install --id EclipseFoundation.Mosquitto",
            ),
            ("Start the service", "net start mosquitto"),
        ]

    manager = _linux_installer()
    return [
        ("Install Mosquitto", manager),
        (
            "Start it now and at boot",
            f"sudo systemctl enable --now {SERVICE}",
        ),
    ]


def _linux_installer() -> str:
    """The install command for whichever package manager is present."""
    for tool, command in (
        ("pacman", "sudo pacman -S mosquitto"),
        ("apt", "sudo apt install mosquitto mosquitto-clients"),
        ("dnf", "sudo dnf install mosquitto"),
        ("zypper", "sudo zypper install mosquitto"),
        ("apk", "sudo apk add mosquitto"),
    ):
        if shutil.which(tool):
            return command
    return "sudo apt install mosquitto mosquitto-clients"


# The account Studio suggests creating on a system broker. A fixed name so
# the instructions are stable across page builds, and a password generated
# once and kept — regenerating on every render would mean the command shown
# and the password recorded could disagree by the time the user pastes it.
SUGGESTED_USER = "studio"

# How long to wait for a CONNACK when testing credentials. Local, so a
# broker that has not answered by now is not going to.
VERIFY_TIMEOUT = 5.0


def suggested_account(backend: Settings | None = None) -> tuple[str, str]:
    """The account to tell the user to create, as (user, password).

    Generated on first ask and then recorded, so that the password printed
    in the instructions is the one Studio will actually authenticate with.
    Recording it before the account exists is deliberate: the alternative is
    asking the user to invent a password and type it into two places.

    Recorded is not the same as working — the account does not exist on the
    broker until the user runs the commands. `verify()` is what settles
    that, and until it passes the page keeps offering the setup step.
    """
    from ....models.config.mqtt_users import generate_password

    store = _backend(backend)
    user = store.get(keys.SYSTEM_BROKER_USER) or SUGGESTED_USER
    password = store.get(keys.SYSTEM_BROKER_PASSWORD)
    if not password:
        password = generate_password()
        store.set(keys.SYSTEM_BROKER_USER, user)
        store.set(keys.SYSTEM_BROKER_PASSWORD, password)
        store.sync()
    return user, password


def verify(backend: Settings | None = None) -> str:
    """Actually connect with the recorded credentials. "" when they work.

    The only honest test. A recorded username and password prove nothing —
    the account lives in /etc/mosquitto/passwd, which Studio cannot read,
    so the sole way to know whether setup worked is to authenticate.
    """
    store = _backend(backend)
    address = host(store)
    number = port(store)
    user, password = credentials(store)
    if not user or not password:
        return "No account recorded yet."

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return "paho-mqtt is not installed."

    from threading import Event

    outcome: dict = {}
    done = Event()

    def on_connect(_client, _userdata, _flags, reason_code, _properties) -> None:
        outcome["code"] = reason_code
        done.set()

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="desk-buddy-studio-verify",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(user, password)
    client.on_connect = on_connect

    target = address if address and port_open(number, address) else "127.0.0.1"
    try:
        client.connect(target, number, keepalive=10)
        client.loop_start()
        if not done.wait(VERIFY_TIMEOUT):
            return f"No response from {target}:{number}."
    except (OSError, ValueError) as error:
        return str(error)
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except (OSError, RuntimeError):
            pass

    code = outcome.get("code")
    if code is not None and getattr(code, "value", code) == 0:
        store.set(keys.SYSTEM_BROKER_VERIFIED, True)
        store.sync()
        return ""

    store.set(keys.SYSTEM_BROKER_VERIFIED, False)
    store.sync()
    return f"The broker refused the account: {code}."


def verified(backend: Settings | None = None) -> bool:
    """Whether the recorded account last authenticated successfully."""
    return bool(_backend(backend).get(keys.SYSTEM_BROKER_VERIFIED))


def password_works(user: str, password: str) -> bool:
    """Whether the broker accepts this pair, right now.

    Studio records the password of every account it creates, but a record
    can go stale: `mosquitto_passwd` run by hand rewrites the hash and
    Studio is never told. A stale record is worse than none, because it
    provisions a robot with a password that looks right and fails minutes
    later as rc=5 on the robot's own log — which is exactly how a desk buddy
    ends up retrying forever against a broker it can reach.

    Cheap enough to ask before handing a credential to a robot: one local
    CONNECT, well under a second.
    """
    if not user or not password:
        return False

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        # Cannot check, so do not claim the password is wrong.
        return True

    from threading import Event

    outcome: dict = {}
    done = Event()
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="desk-buddy-studio-pwcheck",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(user, password)
    client.on_connect = lambda *args: (
        outcome.setdefault("code", args[3]),
        done.set(),
    )

    number = port()
    address = host()
    target = address if address and port_open(number, address) else "127.0.0.1"
    try:
        client.connect(target, number, keepalive=10)
        client.loop_start()
        if not done.wait(VERIFY_TIMEOUT):
            return True  # No answer is not proof of a bad password.
    except (OSError, ValueError):
        return True
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except (OSError, RuntimeError):
            pass

    code = outcome.get("code")
    return code is not None and getattr(code, "value", code) == 0


def account_instructions(user: str, password: str, *, topics: str = "#") -> list[
    tuple[str, str]
]:
    """The commands that create `user` on the system broker.

    The password is included because the user is about to type it into
    `mosquitto_passwd` anyway; hiding it here would only mean they have to
    fetch it from somewhere else in the UI to complete the same step.
    """
    name = shlex.quote(user or "studio")
    secret = shlex.quote(password or "<password>")
    prepare = f"test -e {PASSWD_FILE} || install -m 640 -g mosquitto /dev/null {PASSWD_FILE}"
    acl = shlex.quote(f"user {user or 'studio'}\ntopic readwrite {topics}\n")
    return [
        ("Prepare the password file if missing", "sudo sh -c " + shlex.quote(prepare)),
        (
            "Create the account",
            f"sudo mosquitto_passwd -b {PASSWD_FILE} {name} {secret}",
        ),
        (
            "Let it reach every topic",
            f"printf '%s' {acl} "
            f"| sudo tee -a {ACL_FILE}",
        ),
        (
            "Reload without dropping connections",
            f"sudo systemctl reload {SERVICE}",
        ),
    ]


def listener_instructions(number: int = SYSTEM_PORT) -> list[tuple[str, str]]:
    """What to add so robots on the LAN can reach the broker.

    A default-configured Mosquitto listens on loopback only, which is enough
    for Studio and useless for a robot. This is the one edit that turns it
    into a broker the rest of the network can use.
    """
    directory = CONFIG_DIR / "conf.d"
    fragment = directory / "desk-buddy.conf"
    text = ("# Managed by Desk Buddy Studio\n"
            f"listener {int(number)} 0.0.0.0\nallow_anonymous false\n"
            f"password_file {PASSWD_FILE}\nacl_file {ACL_FILE}\n")
    include = f"include_dir {directory}"
    enable = (f"grep -Fqx {shlex.quote(include)} {CONFIG_FILE} || "
              f"printf '%s\\n' {shlex.quote(include)} >> {CONFIG_FILE}")
    return [
        ("Create the configuration directory", f"sudo install -d -m 755 {directory}"),
        (
            "Configure an authenticated LAN listener",
            f"printf '%s' {shlex.quote(text)} | sudo tee {fragment}",
        ),
        ("Include the configuration once", "sudo sh -c " + shlex.quote(enable)),
        ("Apply it", f"sudo systemctl restart {SERVICE}"),
    ]


# ---- Writing accounts, when the user has allowed it ---------------------
# Mosquitto has no protocol-level way to create an account: `mosquitto_passwd`
# editing a file is the only mechanism there is. So "manage accounts" means
# "write two files in /etc/mosquitto", and whether Studio may do that is a
# filesystem question, asked fresh every time rather than assumed.
#
# Account edits use the existing grant. Privileged setup runs separately;
# administrator passwords are handled exclusively by system authorization.


@dataclass(frozen=True)
class WriteAccess:
    """Whether Studio may edit the broker's account files, and why not."""

    passwd_writable: bool
    acl_writable: bool
    dir_writable: bool
    tool: str
    files_exist: bool

    @property
    def allowed(self) -> bool:
        """Studio can create an account without asking anyone for anything.

        The directory matters as much as the files: `mosquitto_passwd` writes
        its backup alongside the file it edits, so a writable passwd inside a
        read-only directory still fails — with a confusing error about a
        backup, not about permissions.
        """
        return bool(
            self.tool
            and self.files_exist
            and self.passwd_writable
            and self.acl_writable
            and self.dir_writable
        )

    @property
    def reason(self) -> str:
        """Why Studio may not write, in one line. "" when it may."""
        if not self.tool:
            return f"{PASSWD_TOOL} is not installed."
        if not self.files_exist:
            return (
                f"{PASSWD_FILE} does not exist yet — the broker has no "
                "accounts file to edit."
            )
        missing = []
        if not self.passwd_writable:
            missing.append(str(PASSWD_FILE))
        if not self.acl_writable:
            missing.append(str(ACL_FILE))
        if not self.dir_writable:
            missing.append(f"{CONFIG_DIR} (for mosquitto_passwd's backup file)")
        if missing:
            return "Studio cannot write " + ", ".join(missing) + "."
        return ""


def write_access() -> WriteAccess:
    """Can Studio edit the broker's accounts? Checked, never assumed."""
    return WriteAccess(
        passwd_writable=os.access(PASSWD_FILE, os.W_OK),
        acl_writable=os.access(ACL_FILE, os.W_OK),
        dir_writable=os.access(CONFIG_DIR, os.W_OK),
        tool=find(PASSWD_TOOL),
        files_exist=PASSWD_FILE.exists() and ACL_FILE.exists(),
    )


def grant_instructions() -> list[tuple[str, str]]:
    """The one-time commands that let Studio manage accounts itself.

    Ownership rather than 0666: the files hold password hashes and topic
    rules, and handing them to one user is a smaller change than making them
    world-writable. The group stays `mosquitto` so the broker can still read
    them.

    Still the fallback path, and still the whole story — `grant_access()`
    runs exactly these, so a user who would rather read and paste them gets
    the same end state as one who clicks the button.
    """
    user = getpass.getuser()
    return [
        (
            "Let your user own the account files",
            f"sudo chown {user} {PASSWD_FILE} {ACL_FILE}",
        ),
        (
            "Allow mosquitto_passwd to write its backup",
            f"sudo chown {user} {CONFIG_DIR} && sudo chmod u+w {CONFIG_DIR}",
        ),
        (
            "Reload the broker without a password prompt every time",
            f"sudo tee {POLKIT_RULE} <<'EOF'\n{polkit_rule(user)}EOF",
        ),
    ]


def revoke_instructions() -> list[tuple[str, str]]:
    """The commands that undo `grant_instructions()`, for the same reason.

    The fallback wherever pkexec is not available — and the honest answer to
    "what did that button actually do to my system", which a user is most
    likely to want at the moment they are taking it back.
    """
    group = GROUP if _group_exists(GROUP) else "root"
    return [
        (
            "Give the account files back to root",
            f"sudo chown root:{group} {PASSWD_FILE} {ACL_FILE} "
            f"&& sudo chmod 640 {PASSWD_FILE} {ACL_FILE}",
        ),
        (
            "Give the config directory back to root",
            f"sudo chown root {CONFIG_DIR}",
        ),
        (
            "Require a password to reload the broker again",
            f"sudo rm -f {POLKIT_RULE}",
        ),
    ]


# The graphical privilege prompt. pkexec is the one that belongs in a GUI:
# it hands the request to the desktop's polkit agent, which asks in its own
# window, and Studio never sees or handles the password. `sudo` from a GUI
# app has nowhere to prompt, which is why it is not an option here.
ELEVATOR = "pkexec"


def _group_exists(name: str) -> bool:
    """Whether a group is defined on this machine."""
    try:
        import grp

        grp.getgrnam(name)
    except (KeyError, ImportError):
        return False
    return True


def can_grant() -> bool:
    """Whether Studio can offer to fix the permissions itself.

    False on a machine with no polkit agent — a headless login, some minimal
    window managers — where the copy-and-paste steps are the only route.
    """
    return bool(find(ELEVATOR)) and platform.system() == "Linux"


def missing_files() -> list[Path]:
    """The account files the broker needs that do not exist yet."""
    return [path for path in (PASSWD_FILE, ACL_FILE) if not path.exists()]


def grant_access() -> str:
    """Give this user ownership of the broker's account files. "" on success.

    The same two chowns `grant_instructions()` prints, run under pkexec so
    the desktop's own polkit agent does the asking. Studio never collects,
    holds, or passes a password: it asks the system to run one command, and
    the system decides.

    The script is a fixed template with no user input in it. The only
    interpolated values are `getpass.getuser()`, a group name checked
    against the local group database, and paths that are module constants —
    so although this does go through a shell, nothing an attacker controls
    reaches it.

    An account file that does not exist yet is created empty first, because
    `chown` on a missing path fails and "the broker has no accounts file"
    is a setup state rather than an error worth showing the user.
    """
    if not can_grant():
        return (
            f"{ELEVATOR} is not available on this machine. Run the commands "
            "below instead."
        )

    user = getpass.getuser()
    targets = [str(PASSWD_FILE), str(ACL_FILE)]

    # One elevated call, not three: each pkexec invocation is its own
    # authorisation prompt, and being asked to authenticate repeatedly for
    # what the user experienced as a single click is how a prompt stops
    # being read. `install` creates any missing file with the right owner
    # and mode in the same breath as the chowns.
    # Deliberately not `set -e`. Creating a missing file and chowning the
    # directory are independent steps, and a distribution without a
    # `mosquitto` group should not stop the chowns that are the actual point
    # — the group falls back to the user's own, which the broker can still
    # be given access to. What decides success is `write_access()` below,
    # not this script's exit code.
    group = GROUP if _group_exists(GROUP) else user

    # The polkit rule rides along on this same authorisation. It is what
    # stops the *next* prompt: without it every account edit reloads the
    # broker, and every reload asks again. Writing it here means the user is
    # asked once, for the whole capability, rather than once per edit
    # forever — which is both less irritating and safer, since a prompt that
    # appears constantly stops being read.
    #
    # Written via a quoted heredoc, so the rule's own braces and quotes
    # reach the file untouched by the shell.
    rule = polkit_rule(user)
    script = (
        f"for f in {targets[0]} {targets[1]}; do\n"
        f'  [ -e "$f" ] || install -m 640 -o {user} -g {group} /dev/null "$f"\n'
        f"done\n"
        f"chown {user} {targets[0]} {targets[1]}\n"
        f"chown {user} {CONFIG_DIR}\n"
        f"chmod u+w {CONFIG_DIR}\n"
        # Best-effort: a machine with no polkit rules directory still gets
        # working account editing, just with a prompt on each reload.
        f"if [ -d {POLKIT_RULE.parent} ]; then\n"
        f"  cat > {POLKIT_RULE} <<'DESKBUDDY_EOF'\n"
        f"{rule}"
        f"DESKBUDDY_EOF\n"
        f"  chmod 644 {POLKIT_RULE}\n"
        f"fi\n"
    )

    try:
        result = subprocess.run(
            [find(ELEVATOR), "/bin/sh", "-c", script],
            capture_output=True,
            text=True,
            timeout=GRANT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "The permission prompt timed out."
    except (OSError, subprocess.SubprocessError) as error:
        return str(error)

    # 126 is pkexec's own code for "the user dismissed the dialog or was not
    # authorised". Reported as the ordinary outcome it is, not as a failure:
    # deciding not to grant access is a valid answer to being asked.
    if result.returncode == 126:
        return "Cancelled — Studio was not given access."

    # Trust the check, not the exit code: the point is whether Studio can
    # write those files now, and `write_access()` is what actually answers.
    # A partial script that nonetheless got there is a success, and an exit
    # code of 0 that somehow did not is not.
    #
    # The polkit rule is deliberately not part of this test. It is a
    # convenience — without it every edit still works, it just asks for a
    # password first — so a machine that has no rules directory should not
    # report the whole grant as failed over it.
    access = write_access()
    if access.allowed:
        # The rule cannot be read back to confirm it (the rules directory is
        # not readable unprivileged), so trust the script that just ran as
        # root: it writes the rule whenever the directory exists.
        _record_reload_rule(POLKIT_RULE.parent.is_dir())
        return ""

    detail = (result.stderr or result.stdout or "").strip()
    return detail or access.reason or "The permissions did not change."


def revoke_access() -> str:
    """Hand the broker's account files back to root. "" on success.

    The exact inverse of `grant_access()`, and nothing more: the two account
    files go back to root:mosquitto, the config directory back to root, and
    the polkit rule is deleted. Studio can then no longer edit accounts, and
    reloads need a password again — which is the point of asking for it.

    What this deliberately does not touch is anything the grant did not
    create. `mosquitto.conf` keeps whatever ownership it had, accounts
    already in the passwd file stay exactly as they are, and no rule but
    Studio's own is removed. Revoking a permission should not quietly
    rearrange the rest of the broker's setup.

    The mode is restored to 640 rather than guessed at: it is what a
    packaged mosquitto ships, and what the broker needs to read its own
    files while nobody else can.
    """
    if not can_grant():
        return (
            f"{ELEVATOR} is not available on this machine. Run the commands "
            "below instead."
        )

    group = GROUP if _group_exists(GROUP) else "root"
    targets = [str(PASSWD_FILE), str(ACL_FILE)]

    script = (
        f"for f in {targets[0]} {targets[1]}; do\n"
        f'  [ -e "$f" ] && chown root:{group} "$f" && chmod 640 "$f"\n'
        f"done\n"
        f"chown root {CONFIG_DIR}\n"
        f"chmod g-w,o-w {CONFIG_DIR}\n"
        f"rm -f {POLKIT_RULE}\n"
    )

    try:
        result = subprocess.run(
            [find(ELEVATOR), "/bin/sh", "-c", script],
            capture_output=True,
            text=True,
            timeout=GRANT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "The permission prompt timed out."
    except (OSError, subprocess.SubprocessError) as error:
        return str(error)

    if result.returncode == 126:
        return "Cancelled — nothing was changed."

    # Same rule as the grant: the filesystem decides, not the exit code.
    # Success here is Studio *losing* write access.
    if not write_access().allowed:
        _record_reload_rule(False)
        return ""

    detail = (result.stderr or result.stdout or "").strip()
    return detail or "Studio still has write access to the broker's files."


def polkit_rule(user: str = "") -> str:
    """The polkit rule that lets `user` reload the broker without a prompt.

    Deliberately as narrow as a polkit rule gets. It matches on all four of:
    the action (manage-units), the unit (mosquitto.service), the verb
    (reload), and the user — and it only applies to an active local session,
    so it grants nothing over SSH. Anything it does not match falls through
    to the system's normal rules, unchanged.
    """
    name = user or getpass.getuser()
    return (
        "// Installed by Desk Buddy Studio.\n"
        "// Lets this user reload Mosquitto after editing its accounts,\n"
        "// without a password prompt for every single edit.\n"
        "//\n"
        "// Scope: the reload verb, on mosquitto.service, for one user, from\n"
        "// an active local session. Nothing else is granted — not start, not\n"
        "// stop, not any other unit. Delete this file to revoke it.\n"
        "polkit.addRule(function(action, subject) {\n"
        '    if (action.id == "org.freedesktop.systemd1.manage-units" &&\n'
        '        action.lookup("unit") == "mosquitto.service" &&\n'
        '        action.lookup("verb") == "reload" &&\n'
        f'        subject.user == "{name}" &&\n'
        "        subject.local && subject.active) {\n"
        "        return polkit.Result.YES;\n"
        "    }\n"
        "});\n"
    )


def reload_is_silent(backend: Settings | None = None) -> bool:
    """Whether a reload will happen without prompting for a password.

    Read from Studio's own settings rather than from the filesystem. The
    rules directory is root:polkitd 0750 on a normal system, so an
    unprivileged process cannot stat a file inside it at all — `exists()`
    raises PermissionError rather than returning False. Checking the file
    would report "no rule" permanently, including right after writing one,
    which would leave the UI stuck offering a grant that had already
    happened.

    So the flag records what Studio *did*, set when a grant writes the rule
    and cleared when a revoke removes it.
    """
    return bool(_backend(backend).get(keys.SYSTEM_BROKER_RELOAD_RULE))


def _record_reload_rule(installed: bool, backend: Settings | None = None) -> None:
    """Remember whether the polkit rule is in place."""
    store = _backend(backend)
    store.set(keys.SYSTEM_BROKER_RELOAD_RULE, installed)
    store.sync()


def create_account(user: str, password: str, topics: str = "#") -> str:
    """Add `user` to the broker and give it `topics`. "" on success.

    Refuses rather than half-works when Studio has not been granted access:
    the caller is expected to have disabled the button already, and this is
    the second line of defence rather than the first.
    """
    access = write_access()
    if not access.allowed:
        return access.reason or "Studio may not edit the broker's accounts."

    name = user.strip()
    if not name:
        return "A username is required."
    if not password:
        return "A password is required."

    try:
        result = subprocess.run(
            [access.tool, "-b", str(PASSWD_FILE), name, password],
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return str(error)
    if result.returncode != 0:
        return (result.stderr or result.stdout or "").strip() or (
            f"{PASSWD_TOOL} failed."
        )

    problem = _rewrite_acl_entry(name, topics)
    if problem:
        return problem

    # Written down only once the broker has actually taken it, so a failed
    # create never leaves a password recorded for an account that does not
    # exist. This is the only copy: mosquitto_passwd hashed the original.
    _record_password(name, password)
    return reload()


def _record_password(name: str, password: str) -> None:
    """Remember an account's password, so it can be used again.

    Best-effort by design. Failing to write the record must not fail the
    account creation that already succeeded on the broker — the account
    works either way, and the only loss is having to retype the password
    when provisioning a robot.
    """
    from ....models.config.mqtt_users import users

    try:
        users().record(name, password)
    except OSError:
        pass


def _forget_password(name: str) -> None:
    """Drop an account's recorded password once the broker has dropped it."""
    from ....models.config.mqtt_users import users

    try:
        users().forget(name)
    except OSError:
        pass


def remove_account(user: str) -> str:
    """Delete `user` from the broker. "" on success."""
    access = write_access()
    if not access.allowed:
        return access.reason or "Studio may not edit the broker's accounts."

    try:
        result = subprocess.run(
            [access.tool, "-D", str(PASSWD_FILE), user],
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return str(error)
    # -D on an absent user is not a failure worth surfacing: the desired
    # end state is "this account does not exist", which already holds.
    if result.returncode != 0 and "not found" not in (result.stderr or "").lower():
        return (result.stderr or result.stdout or "").strip() or (
            f"{PASSWD_TOOL} failed."
        )

    problem = _rewrite_acl_entry(user, topics=None)
    if problem:
        return problem

    # The account is gone from the broker, so the recorded password is now a
    # secret for nothing. Keeping it would also mean a later account reusing
    # the name would inherit a password that does not work.
    _forget_password(user)
    return reload()


@dataclass(frozen=True)
class Account:
    """One account as the broker itself has it.

    `password` is what Studio recorded when it created the account, and is
    empty for one it did not create (or one whose password was changed with
    `mosquitto_passwd` behind its back). The broker keeps only a hash, so
    this is the sole way a password is ever known a second time — and
    without it, provisioning a robot means asking the user for a secret
    that was generated for them and shown once.
    """

    name: str
    topics: tuple[str, ...] = ()
    password: str = ""

    @property
    def topics_display(self) -> str:
        return ", ".join(self.topics) if self.topics else "(no topics)"

    @property
    def full_access(self) -> bool:
        return tuple(self.topics) == ("#",)

    @property
    def access(self) -> str:
        return "Full access" if self.full_access else "Own topics"


def accounts() -> list[Account]:
    """Every account the broker knows, read from its passwd and acl files.

    The broker is the source of truth. An account created by hand with
    `mosquitto_passwd`, or by anything else on this machine, is as real as
    one Studio made — reading the files is what makes those visible instead
    of Studio showing a private list that quietly disagrees with reality.

    Names come from passwd (an account exists if it can authenticate) and
    topics from acl. An account in passwd with no ACL block is listed with
    no topics, which is exactly what it can reach.
    """
    names: list[str] = []
    try:
        for line in PASSWD_FILE.read_text().splitlines():
            name = line.split(":", 1)[0].strip()
            if name and not name.startswith("#"):
                names.append(name)
    except OSError:
        return []

    rules = _acl_topics()
    secrets = _recorded_passwords()
    return [
        Account(
            name=name,
            topics=tuple(rules.get(name, ())),
            password=secrets.get(name, ""),
        )
        for name in names
    ]


def _recorded_passwords() -> dict[str, str]:
    """Passwords Studio generated, by account name.

    The broker stays the source of truth for *which* accounts exist — this
    only supplies the one thing its files cannot give back. An account
    recorded here but absent from passwd is simply not returned, so a stale
    record can never invent an account that does not exist.
    """
    from ....models.config.mqtt_users import users

    try:
        return {user.name: user.password for user in users().all() if user.password}
    except OSError:
        return {}


def _acl_topics() -> dict[str, list[str]]:
    """Topic filters per user, as the acl file has them."""
    found: dict[str, list[str]] = {}
    try:
        lines = ACL_FILE.read_text().splitlines()
    except OSError:
        return found

    current = ""
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("user "):
            current = stripped[5:].strip()
            found.setdefault(current, [])
            continue
        if stripped.startswith("topic ") and current:
            parts = stripped.split()
            # "topic <access> <filter>" or "topic <filter>"
            if len(parts) >= 3 and parts[1] in ("read", "write", "readwrite"):
                found[current].append(" ".join(parts[2:]))
            elif len(parts) >= 2:
                found[current].append(" ".join(parts[1:]))
    return found


def set_topics(user: str, topics: tuple[str, ...] | list[str]) -> str:
    """Replace `user`'s topic list on the broker. "" on success.

    Only the ACL is touched — no password is involved, so this works for an
    account Studio did not create and has no secret for.
    """
    access = write_access()
    if not access.acl_writable:
        return access.reason or "Studio may not edit the broker's ACL."

    problem = _rewrite_acl_entry(user, ",".join(topics) if topics else "")
    if problem:
        return problem
    return reload()


def _rewrite_acl_entry(user: str, topics: str | None) -> str:
    """Replace `user`'s block in the ACL, or remove it when topics is None.

    The file is rewritten rather than appended to, so that editing an account
    twice does not leave two blocks for it — Mosquitto reads the first and
    silently ignores the second, which looks exactly like a change that did
    not take.

    Only this user's block is touched. Accounts Studio has never heard of —
    ones the user created by hand — keep their rules and their position.
    """
    try:
        existing = ACL_FILE.read_text().splitlines()
    except OSError as error:
        return str(error)

    lines: list[str] = []
    skipping = False
    for line in existing:
        stripped = line.strip()
        if stripped.startswith("user "):
            skipping = stripped[5:].strip() == user
            if skipping:
                continue
        elif skipping:
            # Inside the block being replaced: its topic lines go with it,
            # and a blank line ends it.
            if not stripped or stripped.startswith("topic "):
                continue
            skipping = False
        lines.append(line)

    while lines and not lines[-1].strip():
        lines.pop()

    if topics is not None:
        if lines:
            lines.append("")
        lines.append(f"user {user}")
        for topic in _topic_list(topics):
            lines.append(f"topic readwrite {topic}")

    try:
        ACL_FILE.write_text("\n".join(lines) + "\n")
    except OSError as error:
        return str(error)
    return ""


def _topic_list(topics: str) -> list[str]:
    """Accept either one filter or a comma-separated list of them."""
    return [part.strip() for part in topics.split(",") if part.strip()] or ["#"]


def reload() -> str:
    """Ask the broker to re-read its account files. "" on success.

    SIGHUP via the service manager, so nobody is disconnected. This is the
    one step that may still need privilege — but on a desktop with a polkit
    agent it is normally allowed for the active session, and a failure here
    leaves the accounts correctly written and only not yet live, which the
    caller reports as such rather than as a lost change.
    """
    from .setup_platform import detect, service_commands
    store = settings()
    store.set(keys.SYSTEM_BROKER_RELOAD_PENDING, True)
    store.sync()
    try:
        commands = service_commands(detect().service_manager, "reload")
    except RuntimeError as error:
        return f"Accounts written, but the broker could not be reloaded: {error}"
    try:
        for command in commands:
            result = subprocess.run(command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT)
            if result.returncode:
                return "Accounts written, but the service manager refused the reload. Open Network → Broker and retry setup to apply them."
    except (OSError, subprocess.SubprocessError) as error:
        return f"Accounts written, but the broker could not be reloaded: {error}"
    store.set(keys.SYSTEM_BROKER_RELOAD_PENDING, False)
    store.sync()
    return ""
