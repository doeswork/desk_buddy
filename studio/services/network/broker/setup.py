"""Automatic broker setup, independent of widgets and their rebuild cycle."""
from __future__ import annotations

import json
import queue
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from .setup_platform import Environment, detect, executable, firewalld_zone, run


@dataclass(frozen=True)
class SetupStatus:
    state: str = "idle"
    message: str = "Studio will connect and set up the broker when Network opens."
    connected: bool = False
    robot_host: str = ""
    network_ready: bool = False
    detail: str = ""
    user: str = ""
    reload_rule: bool = False
    access_revoked: bool = False


@dataclass(frozen=True)
class AccountReloadStatus:
    """Lifecycle of applying already-written passwd and ACL changes."""

    state: str = "idle"
    message: str = "Broker user changes are up to date."
    detail: str = ""


class SetupCancelled(RuntimeError):
    pass


def redact(message: str, secret: str) -> str:
    """Remove a generated credential without failing on malformed input."""
    return message.replace(secret, "[hidden]") if secret else message


def verify_connection(host: str, port: int, user: str, password: str) -> str:
    """Prove authentication and both ACL directions using a fresh private topic."""
    if not user or not password:
        return "Studio needs a broker account."
    from .finder import port_open
    if not port_open(port, host):
        return "The broker is not answering."
    import paho.mqtt.client as mqtt
    done = threading.Event()
    token = uuid.uuid4().hex
    topic = f"desk-buddy/setup/{token}"
    problem = ["The broker did not complete the connection test."]
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"studio-check-{token[:12]}", protocol=mqtt.MQTTv311)
    client.username_pw_set(user, password)

    def connected(client, _data, _flags, reason, _properties):
        if getattr(reason, "value", reason) != 0:
            problem[0] = "The broker refused Studio’s credentials."
            done.set()
        else:
            result, _ = client.subscribe(topic, qos=1)
            if result:
                problem[0] = "The broker refused the test subscription."
                done.set()

    def subscribed(client, _data, _mid, reasons, _properties):
        if any(getattr(reason, "value", reason) >= 128 for reason in reasons):
            problem[0] = "The broker account cannot subscribe."
            done.set()
            return
        client.publish(topic, token, qos=1, retain=False)

    def received(_client, _data, message):
        if message.topic == topic and message.payload == token.encode():
            problem[0] = ""
            done.set()

    client.on_connect = connected
    client.on_subscribe = subscribed
    client.on_message = received
    try:
        client.connect_async(host, port, keepalive=10)
        client.loop_start()
        done.wait(7)
    except (OSError, ValueError, RuntimeError):
        problem[0] = "Could not connect to the MQTT broker."
    finally:
        client.disconnect()
        client.loop_stop()
    return problem[0]


def helper_command(request: Path, result: Path) -> list[str]:
    args = ["--broker-setup-helper", str(request), str(result)]
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return [sys.executable, *args]
    # Absolute module entry script makes the terminal independent of cwd.
    return [sys.executable, str(Path(__file__).resolve().parents[3] / "setup_entry.py"), *args]


def terminal_command(env: Environment, script: Path) -> list[str]:
    command = ["/bin/sh", str(script)]
    if env.wsl and env.distribution and executable("wt.exe"):
        return [executable("wt.exe"), "new-tab", "--title", "Desk Buddy setup",
                "wsl.exe", "--distribution", env.distribution, "--exec", *command]
    if env.wsl and env.distribution and executable("powershell.exe"):
        # Start a console even when Windows Terminal is not installed.
        import base64
        args = subprocess.list2cmdline(["--distribution", env.distribution, "--exec", *command])
        ps = "Start-Process wsl.exe -ArgumentList '" + args.replace("'", "''") + "'"
        return [executable("powershell.exe"), "-NoProfile", "-EncodedCommand", base64.b64encode(ps.encode("utf-16le")).decode()]
    for name, flag in (("x-terminal-emulator", "-e"), ("gnome-terminal", "--"), ("konsole", "-e"), ("xfce4-terminal", "-x"), ("xterm", "-e")):
        if executable(name):
            return [executable(name), flag, *command]
    raise RuntimeError("No authorization terminal is available. Install a desktop authentication agent or terminal, then retry. Manual commands are in Advanced / Manual setup.")


def authorize(request: dict, env: Environment, emit) -> dict:
    """One root helper, with a desktop prompt or an external sudo terminal.

    Private files carry generated credentials and progress; neither the terminal
    nor the GUI receives the administrator password. The helper publishes each
    stage atomically, including its final result.
    """
    with tempfile.TemporaryDirectory(prefix="desk-buddy-setup-") as directory:
        root = Path(directory)
        source, result = root / "request.json", root / "result.json"
        source.write_text(json.dumps(request))
        source.chmod(0o600)
        command = helper_command(source, result)
        process = None
        wsl = executable("wsl.exe") if env.wsl and env.distribution else ""
        if wsl:
            # Windows already grants its signed-in user administrative control
            # over their WSL distributions. Run only our narrow, validated
            # Linux broker helper as WSL root. Windows firewall and forwarding
            # are managed separately and are not part of broker setup.
            emit(
                "applying" if request.get("action") == "reload" else "checking",
                "Applying broker user changes inside WSL…"
                if request.get("action") == "reload"
                else "Preparing the broker inside WSL…",
            )
            process = subprocess.Popen(
                [wsl, "--distribution", env.distribution, "--user", "root",
                 "--exec", *command],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif executable("pkexec"):
            emit(
                "applying" if request.get("action") == "reload" else "checking",
                "Approve applying broker user changes…"
                if request.get("action") == "reload"
                else "Approve broker setup in the system authorization dialog…",
            )
            process = subprocess.Popen([executable("pkexec"), "--disable-internal-agent", *command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # A desktop-less polkit rejects quickly. Fall back only on failure,
            # never after the user explicitly cancels (126).
            prompt_deadline = time.monotonic() + 120
            while process.poll() is None and not result.exists():
                if time.monotonic() >= prompt_deadline:
                    process.terminate()
                    raise SetupCancelled("The authorization prompt timed out. Retry setup when you’re ready.")
                time.sleep(0.1)
            if process.poll() == 126:
                raise SetupCancelled("Authorization cancelled. Retry setup when you’re ready.")
            if process.poll() not in (None, 0) and not result.exists():
                process = None
        if process is None:
            reloading = request.get("action") == "reload"
            emit(
                "applying" if reloading else "checking",
                "Approve applying broker user changes in the terminal that opens…"
                if reloading
                else "Approve broker setup in the terminal that opens…",
            )
            script = root / "approve.sh"
            exit_file = root / "terminal-exit"
            script.write_text("#!/bin/sh\ntrap " + shlex.quote("printf 1 > " + shlex.quote(str(exit_file))) + " HUP INT TERM\n" +
                              "printf '%s\\n' " + shlex.quote(
                                  "Desk Buddy: apply broker user changes."
                                  if reloading
                                  else "Desk Buddy: install and configure the MQTT broker."
                              ) + "\n" +
                              "sudo -- " + shlex.join(command) + "\ncode=$?\n" +
                              "printf '%s' \"$code\" > " + shlex.quote(str(exit_file)) + "\n")
            script.chmod(0o700)
            subprocess.Popen(terminal_command(env, script), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 1200
        last = None
        while time.monotonic() < deadline:
            if result.exists():
                data = json.loads(result.read_text())
                if data.get("state") == "complete":
                    return data
                if data.get("state") == "failed":
                    raise RuntimeError(data["message"])
                if data != last:
                    emit(data["state"], data["message"])
                    last = data
            if process is not None and process.poll() is not None and (not result.exists() or json.loads(result.read_text()).get("state") not in ("complete", "failed")):
                raise RuntimeError("The setup helper did not start. Open Advanced / Manual setup for alternatives.")
            if (root / "terminal-exit").exists():
                raise SetupCancelled("Terminal authorization was cancelled or unsuccessful. Retry setup when you’re ready.")
            time.sleep(0.2)
        raise RuntimeError("Setup timed out. Close the authorization terminal before retrying.")


def local_network() -> tuple[str, str]:
    """Use the default-route interface; exclude unrelated VPN/container routes."""
    try:
        routes = json.loads(run(["ip", "-j", "route", "get", "1.1.1.1"]).stdout)
        route = routes[0]
        address = route.get("prefsrc", "")
        links = json.loads(run(["ip", "-j", "address", "show", "dev", route["dev"]]).stdout)
        import ipaddress
        for info in links[0]["addr_info"]:
            if info.get("family") == "inet" and info["local"] == address:
                return address, str(ipaddress.IPv4Network(f"{address}/{info['prefixlen']}", strict=False))
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        pass
    return "", ""


def firewall_ready(number: int, subnet: str) -> bool:
    from .finder import _ufw_enabled, _ufw_allows
    firewall = "ufw" if _ufw_enabled() else ""
    if not firewall and executable("firewall-cmd") and run(["firewall-cmd", "--state"]).returncode == 0:
        firewall = "firewalld"
    if not firewall:
        return True
    if firewall == "ufw":
        verdict = _ufw_allows(number)
        return verdict.allowed and (" from " not in verdict.rule or verdict.rule.endswith(" from " + subnet))
    if firewall == "firewalld":
        rule = f'rule family="ipv4" source address="{subnet}" port port="{number}" protocol="tcp" accept'
        zone = "--zone=" + firewalld_zone()
        return all(run(["firewall-cmd", zone, *flags, "--query-rich-rule", rule]).returncode == 0 for flags in ([], ["--permanent"]))
    return False


def perform(request: dict, emit) -> SetupStatus:
    """Idempotent attempt. UI retry policy lives in Coordinator, not here."""
    env = detect()
    from . import system
    host = request.get("host") or "127.0.0.1"
    name, secret, number = request["user"], request["password"], request["port"]
    emit("checking", "Checking the broker connection…")
    if request.get("action") == "revoke":
        authorize(request, env, emit)
        connected = not verify_connection(host, number, name, secret)
        return SetupStatus("cancelled", "Account management is revoked. Automatic setup is paused.",
                           connected, request.get("robot_host", ""), request.get("network_ready", False),
                           user=name, access_revoked=True)
    problem = verify_connection(host, number, name, secret)
    connected = not problem
    address, subnet = local_network() if env.linux else ("", "")
    remote = bool(request.get("host")) and host not in ("localhost", "127.0.0.1", "::1", address)
    if remote:
        return SetupStatus("ready" if connected else "failed", "Connected to the configured broker." if connected else problem,
                           connected, host if connected else "", connected, user=name)
    if not env.linux:
        return SetupStatus("ready" if connected else "failed", "Connected." if connected else "Automatic setup is available on Linux and WSL. Open Advanced / Manual setup.", connected, user=name)
    # A stock loopback-only broker may accept every supplied username. That
    # does not prove the suggested account exists or its password is correct.
    authenticated = connected and bool(verify_connection(host, number, name, uuid.uuid4().hex))
    network_problem = ""
    reload_rule = False
    # Reuse a working authenticated broker, including a custom configuration.
    # A successful loopback connection alone cannot establish a LAN listener.
    from .setup_helper import FRAGMENT, MARKER
    try:
        managed = FRAGMENT.read_text().startswith(MARKER)
    except OSError:
        managed = False
    grant_ready = not managed or system.write_access().allowed
    public = bool(address) and not verify_connection(address, number, name, secret) if connected else False
    try:
        network_allowed = firewall_ready(number, subnet) if connected and subnet else False
    except (OSError, RuntimeError, subprocess.SubprocessError):
        network_allowed = False  # The authorized helper can inspect protected rules.
    if not (authenticated and public and network_allowed and grant_ready):
        action = "network" if authenticated and public and grant_ready else "setup"
        try:
            data = authorize({**request, "action": action, "reuse": authenticated, "subnet": subnet}, env, emit)
        except (RuntimeError, OSError, subprocess.SubprocessError) as error:
            if action != "network":
                raise
            return SetupStatus("cancelled" if isinstance(error, SetupCancelled) else "failed",
                               "Studio is connected. Robot network setup is incomplete.", True,
                               detail=str(error), user=name)
        name = data["user"]
        reload_rule = data.get("reload_rule", False)
        network_problem = data.get("network_problem", "")
    emit("verifying", "Verifying Studio’s connection…")
    problem = verify_connection(host, number, name, secret)
    if problem:
        return SetupStatus("failed", problem, user=name, reload_rule=reload_rule)
    status = SetupStatus("ready", f"Connected as “{name}” at {host}:{number}.", True, address, bool(address) and not network_problem,
                         network_problem, name, reload_rule)
    if env.wsl:
        # The broker lifecycle ends at the Linux boundary.  A separate,
        # explicitly requested WSL access coordinator inspects or changes the
        # Windows host, so declining UAC can never turn this successful local
        # setup into a broker failure.  Never advertise WSL's private address.
        status = replace(status, robot_host="", network_ready=False)
        if not network_problem:
            return status
    if not status.network_ready and status.state == "ready":
        status = replace(status, state="failed", message="Studio is connected. Robot network setup is incomplete.",
                         detail=network_problem or "No LAN address is available. Connect to the robot’s network, then retry.")
    return status


class Coordinator:
    """One background attempt; refreshes cannot launch another authorization."""
    def __init__(self, operation=perform):
        self.operation = operation
        self.status = SetupStatus()
        self.events: queue.Queue = queue.Queue()
        self.running = False
        self.attempted = False
        self.paused = False

    def start(self, request: dict, *, retry: bool = False) -> bool:
        if self.running or (not retry and (self.attempted or self.paused)):
            return False
        self.running = self.attempted = True
        self.paused = False
        self.status = SetupStatus("checking", "Checking the broker connection…")

        def emit(state, message, **fields):
            self.events.put(("progress", state, message, fields))

        def work():
            try:
                result = self.operation(dict(request), emit)
            except Exception as error:
                result = SetupStatus("cancelled" if isinstance(error, SetupCancelled) else "failed",
                                     "Broker setup needs attention.",
                                     detail=redact(
                                         str(error),
                                         str(request.get("password") or ""),
                                     ))
            self.events.put(("result", result))

        threading.Thread(target=work, name="broker-setup", daemon=True).start()
        return True

    def poll(self) -> bool:
        changed = False
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                return changed
            changed = True
            if event[0] == "result":
                self.status = event[1]
                self.running = False
            else:
                self.status = replace(self.status, state=event[1], message=event[2], **event[3])


def apply_account_reload(request: dict, emit) -> AccountReloadStatus:
    """Apply pending account files without changing broker setup state."""
    from . import system

    environment = detect()
    emit("applying", "Applying broker user changes…")
    if not request.get("direct_attempted"):
        direct = system.reload()
        if direct.applied:
            return AccountReloadStatus(
                "ready", "Broker user changes are active."
            )

    print(
        "[Studio broker accounts] requesting the privileged reload helper",
        flush=True,
    )
    authorize({**request, "action": "reload"}, environment, emit)
    print("[Studio broker accounts] privileged reload complete", flush=True)
    return AccountReloadStatus("ready", "Broker user changes are active.")


class AccountReloadCoordinator:
    """A serialized account reload, independent of broker setup health."""

    def __init__(self, operation=apply_account_reload):
        self.operation = operation
        self.status = AccountReloadStatus()
        self.events: queue.Queue = queue.Queue()
        self.running = False
        self.attempted = False

    def start(self, request: dict, *, retry: bool = False) -> bool:
        if self.running or (self.attempted and not retry):
            return False
        self.running = self.attempted = True
        self.status = AccountReloadStatus(
            "applying", "Applying broker user changes…"
        )

        def emit(state, message, **fields):
            self.events.put(("progress", state, message, fields))

        def work():
            try:
                result = self.operation(dict(request), emit)
            except Exception as error:
                detail = redact(
                    str(error), str(request.get("password") or "")
                )
                state = "cancelled" if isinstance(error, SetupCancelled) else "failed"
                print(
                    f"[Studio broker accounts] privileged reload {state}: "
                    f"{type(error).__name__}: {detail}",
                    flush=True,
                )
                result = AccountReloadStatus(
                    state,
                    "Broker user changes still need to be applied.",
                    detail,
                )
            self.events.put(("result", result))

        threading.Thread(
            target=work, name="broker-account-reload", daemon=True
        ).start()
        return True

    def poll(self) -> bool:
        changed = False
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                return changed
            changed = True
            if event[0] == "result":
                self.status = event[1]
                self.running = False
            else:
                self.status = replace(
                    self.status,
                    state=event[1],
                    message=event[2],
                    **event[3],
                )
