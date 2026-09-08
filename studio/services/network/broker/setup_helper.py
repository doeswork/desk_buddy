"""Narrow privileged Linux setup entry point (also supported in frozen builds).

The request contains data, never shell commands or arbitrary destination paths.
All output is structured and excludes passwords. Tests substitute the fixed
paths and command runner; importing this module has no side effects.
"""
from __future__ import annotations

import grp
import ipaddress
import json
import os
import pwd
import re
import shutil
import stat
import tempfile
import time
from pathlib import Path

from .setup_platform import detect, executable, firewalld_zone, install_commands, run, service_commands

CONFIG_DIR = Path("/etc/mosquitto")
CONFIG = CONFIG_DIR / "mosquitto.conf"
FRAGMENT = CONFIG_DIR / "conf.d/desk-buddy.conf"
PASSWD = CONFIG_DIR / "passwd"
ACL = CONFIG_DIR / "acl"
RULE = Path("/etc/polkit-1/rules.d/49-desk-buddy-mosquitto.rules")
MARKER = "# Managed by Desk Buddy Studio"


def checked(command: list[str], *, timeout: int = 30) -> None:
    result = run(command, timeout=timeout)
    if result.returncode:
        # Deliberately do not relay utility output, which can contain secrets.
        raise RuntimeError(f"{Path(command[0]).name} failed (exit {result.returncode}). Check the system service/package logs in Advanced / Manual setup.")


def configuration(number: int) -> tuple[str, str]:
    """Preserve compatible existing config; refuse ambiguous authentication.

    Follow include_dir recursively and inspect the complete configuration, not
    just the distro's main file. Never add a duplicate listener or auth setting.
    """
    main = CONFIG.read_text() if CONFIG.exists() else ""
    entries: list[tuple[str, str]] = []
    visited: set[Path] = set()

    def read(path: Path) -> None:
        path = path.resolve()
        if path in visited or path == FRAGMENT.resolve():
            return
        visited.add(path)
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split(None, 1)
            key, value = fields[0], fields[1] if len(fields) == 2 else ""
            if key == "include_dir":
                directory = Path(value)
                if not directory.is_absolute():
                    raise RuntimeError("A relative Mosquitto include_dir needs manual review.")
                for child in sorted(directory.glob("*.conf")):
                    read(child)
            else:
                entries.append((key, value))

    if CONFIG.exists():
        read(CONFIG)
    if FRAGMENT.exists() and not FRAGMENT.read_text().startswith(MARKER):
        raise RuntimeError("desk-buddy.conf already exists and is not owned by Studio.")
    values: dict[str, list[str]] = {}
    for key, value in entries:
        values.setdefault(key, []).append(value)
    unsupported = {"plugin", "auth_plugin", "per_listener_settings", "port", "bind_address", "psk_file", "cafile", "capath", "certfile", "keyfile", "require_certificate", "connection"}
    if unsupported.intersection(values):
        raise RuntimeError("The broker uses custom authentication or listeners. Preserve that configuration and connect with an existing account using Advanced / Manual setup.")
    if "user" in values and values["user"] != ["mosquitto"]:
        raise RuntimeError("The broker runs as a custom system user. Its file permissions require manual configuration.")
    listeners = values.get("listener", [])
    if len(listeners) > 1:
        raise RuntimeError("Multiple existing listeners require manual configuration.")
    if listeners and listeners[0].split() not in ([str(number)], [str(number), "0.0.0.0"]):
        raise RuntimeError("The existing listener uses another port or interface. Review it in Advanced / Manual setup.")
    expected = {"password_file": str(PASSWD), "acl_file": str(ACL), "allow_anonymous": "false"}
    for key, value in expected.items():
        if key in values and values[key] != [value]:
            raise RuntimeError(f"Existing {key} conflicts with automatic setup. Review the broker configuration in Advanced / Manual setup.")
    fragment = [MARKER]
    if not listeners:
        fragment.append(f"listener {number} 0.0.0.0")
    fragment.extend(f"{key} {value}" for key, value in expected.items() if key not in values)
    # include_dir is processed after the main file. Add our own directory once.
    if FRAGMENT.parent.resolve() not in {
        Path(line.split(None, 1)[1]).resolve()
        for path in visited for line in path.read_text().splitlines()
        if line.split() and line.split()[0] == "include_dir" and len(line.split(None, 1)) == 2
    }:
        main = main.rstrip() + f"\n{MARKER}\ninclude_dir {FRAGMENT.parent}\n"
    return main, "\n".join(fragment) + "\n"


class Backup:
    """Root-private transaction backup; restore content, mode and ownership."""
    def __init__(self, paths: list[Path]):
        self.directory = Path(tempfile.mkdtemp(prefix="desk-buddy-backup-"))
        self.records = []
        for index, path in enumerate(paths):
            if path.is_symlink():
                raise RuntimeError(f"Refusing to replace symbolic link: {path}")
            if path.exists():
                info = path.stat()
                target = self.directory / str(index)
                shutil.copy2(path, target)
                self.records.append((path, target, info))
            else:
                self.records.append((path, None, None))

    def restore(self) -> None:
        for path, copy, info in reversed(self.records):
            if copy is None:
                path.unlink(missing_ok=True)
            else:
                shutil.copy2(copy, path)
                os.chown(path, info.st_uid, info.st_gid)

    def close(self) -> None:
        shutil.rmtree(self.directory)


def acl_users() -> set[str]:
    """Names reserved by ACL blocks, including orphaned entries."""
    try:
        lines = ACL.read_text().splitlines()
    except OSError:
        return set()
    return {
        stripped[5:].strip()
        for line in lines
        if (stripped := line.strip()).startswith("user ")
        and stripped[5:].strip()
    }


def grant_full_access(name: str) -> None:
    """Replace only Studio's ACL block with its required full-access rule."""
    try:
        existing = ACL.read_text().splitlines()
    except OSError:
        existing = []
    lines: list[str] = []
    skipping = False
    for line in existing:
        stripped = line.strip()
        if stripped.startswith("user "):
            skipping = stripped[5:].strip() == name
            if skipping:
                continue
        elif skipping:
            # A Mosquitto user block lasts until the next ``user`` directive,
            # including comments and blank lines. Dropping only topic lines
            # could leave an old rule attached to a comment-heavy block.
            continue
        lines.append(line)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines:
        lines.append("")
    lines.extend((f"user {name}", "topic readwrite #"))
    ACL.write_text("\n".join(lines) + "\n")


def firewall(number: int, subnet: str) -> None:
    """Add a port/subnet rule only to a firewall which is already active."""
    if not subnet:
        return
    subnet = str(ipaddress.IPv4Network(subnet, strict=False))
    if executable("ufw") and "Status: active" in run(["ufw", "status"]).stdout:
        checked(["ufw", "allow", "from", subnet, "to", "any", "port", str(number), "proto", "tcp", "comment", "Desk Buddy MQTT"])
    elif executable("firewall-cmd") and run(["firewall-cmd", "--state"]).returncode == 0:
        rule = f'rule family="ipv4" source address="{subnet}" port port="{number}" protocol="tcp" accept'
        zone = "--zone=" + firewalld_zone()
        checked(["firewall-cmd", zone, "--permanent", "--add-rich-rule", rule])
        checked(["firewall-cmd", zone, "--add-rich-rule", rule])


def provision(request: dict, emit) -> dict:
    env = detect()
    if not env.linux:
        raise RuntimeError("Automatic provisioning is supported on Linux and WSL.")
    number = int(request["port"])
    if not 1 <= number <= 65535:
        raise ValueError("Invalid broker port.")
    uid = int(request["uid"])
    owner = pwd.getpwuid(uid)
    name = request["user"]
    secret = request["password"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name) or not secret or any(c in secret for c in "\r\n\x00"):
        raise ValueError("Invalid account details.")
    action = request.get("action", "setup")
    if action not in ("setup", "network", "revoke", "reload"):
        raise ValueError("Unsupported setup action.")
    if action == "reload":
        emit("starting", "Applying broker account changes…")
        for command in service_commands(env.service_manager, "reload"):
            checked(command)
        return {"user": name, "reload_rule": False, "network_problem": ""}
    if action == "revoke":
        emit("configuring", "Revoking account-management access…")
        gid = grp.getgrnam("mosquitto").gr_gid
        for path in (PASSWD, ACL, CONFIG_DIR, RULE):
            if path.is_symlink():
                raise RuntimeError("A broker path is a symbolic link and needs manual review.")
        for path in (PASSWD, ACL):
            if path.exists():
                os.chown(path, 0, gid)
                os.chmod(path, 0o640)
        os.chown(CONFIG_DIR, 0, CONFIG_DIR.stat().st_gid)
        RULE.unlink(missing_ok=True)
        return {"user": name, "reload_rule": False, "revoked": True}
    if action == "network":
        emit("verifying", "Preparing local network access…")
        firewall(number, request.get("subnet", ""))
        return {"user": name, "reload_rule": False, "network_problem": ""}
    if not executable("mosquitto") or not executable("mosquitto_passwd"):
        emit("installing", "Installing Mosquitto…")
        for command in install_commands(env.package_manager):
            checked(command, timeout=900)
        env = detect()
    service_commands(env.service_manager, "start")  # Fail before changing files.
    emit("configuring", "Preparing the broker and Studio’s account…")
    main, fragment = configuration(number)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    FRAGMENT.parent.mkdir(parents=True, exist_ok=True)
    directory_info = CONFIG_DIR.stat()
    paths = [CONFIG, FRAGMENT, PASSWD, ACL]
    if RULE.parent.is_dir():
        paths.append(RULE)
    backup = Backup(paths)
    try:
        gid = grp.getgrnam("mosquitto").gr_gid
        existing = {line.partition(":")[0] for line in PASSWD.read_text().splitlines()} if PASSWD.exists() else set()
        reserved = existing | acl_users()
        reuse = bool(request.get("reuse")) and name in existing
        if not reuse:
            base = name
            suffix = 2
            while name in reserved:
                name = f"{base}-{suffix}"
                suffix += 1
        for path in (PASSWD, ACL):
            if not path.exists():
                path.touch(mode=0o640)
            os.chown(path, uid, gid)
            os.chmod(path, 0o640)
        if not reuse:
            checked([executable("mosquitto_passwd"), "-b", str(PASSWD), name, secret])
        # A working password does not prove the account can reach Studio's
        # complete MQTT surface. Preserve every other block and make this
        # controller identity's required permission exact.
        grant_full_access(name)
        for path in (PASSWD, ACL):
            os.chown(path, uid, gid)
            os.chmod(path, 0o640)
        # Match the existing account-management grant/revoke contract.
        os.chown(CONFIG_DIR, uid, directory_info.st_gid)
        os.chmod(CONFIG_DIR, stat.S_IMODE(directory_info.st_mode) | stat.S_IWUSR)
        CONFIG.write_text(main)
        FRAGMENT.write_text(fragment)
        os.chmod(CONFIG, 0o644)
        os.chmod(FRAGMENT, 0o644)
        if executable("restorecon"):
            checked(["restorecon", str(CONFIG), str(FRAGMENT), str(PASSWD), str(ACL)])
        rule_installed = RULE.parent.is_dir() and env.service_manager == "systemd"
        if rule_installed:
            from .system import polkit_rule
            RULE.write_text(polkit_rule(owner.pw_name))
            os.chmod(RULE, 0o644)
        emit("starting", "Starting the broker…")
        for command in service_commands(env.service_manager, "start"):
            checked(command)
        # A service exit status alone does not establish MQTT readiness.
        from .setup import verify_connection
        problem = ""
        for _ in range(3):
            problem = verify_connection("127.0.0.1", number, name, secret)
            if not problem:
                break
            time.sleep(0.5)
        if problem:
            raise RuntimeError(problem)
    except Exception:
        backup.restore()
        os.chown(CONFIG_DIR, directory_info.st_uid, directory_info.st_gid)
        os.chmod(CONFIG_DIR, stat.S_IMODE(directory_info.st_mode))
        for command in service_commands(env.service_manager, "restart"):
            run(command)
        raise
    finally:
        backup.close()
    # Network failure should not roll back a working local broker/account.
    network_problem = ""
    try:
        firewall(number, request.get("subnet", ""))
    except (RuntimeError, OSError, ValueError) as error:
        network_problem = str(error)
    return {"user": name, "reload_rule": rule_installed, "network_problem": network_problem}


def main(request_path: str, result_path: str) -> int:
    """Only called via --broker-setup-helper after system authorization."""
    request_file = Path(request_path)
    result_file = Path(result_path)
    info = request_file.lstat()
    if os.geteuid() != 0 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        return 1
    if result_file.parent != request_file.parent or result_file.is_symlink():
        return 1
    directory_info = request_file.parent.lstat()
    if not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_uid != info.st_uid or directory_info.st_mode & 0o077:
        return 1
    request = json.loads(request_file.read_text())
    if int(request["uid"]) != info.st_uid:
        return 1
    uid = info.st_uid

    def output(data: dict) -> None:
        descriptor, filename = tempfile.mkstemp(prefix="result-", dir=result_file.parent)
        temporary = Path(filename)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(data, stream)
                os.fchown(stream.fileno(), uid, info.st_gid)
            temporary.replace(result_file)
        finally:
            temporary.unlink(missing_ok=True)

    lock = None
    try:
        import fcntl
        # Serialize root helpers across app windows and interrupted retries.
        Path("/run/lock").mkdir(parents=True, exist_ok=True)
        lock = os.open("/run/lock/desk-buddy-broker-setup.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock)
            lock = None
            raise RuntimeError("Another broker setup is still running. Wait for it to finish before retrying.")
        result = provision(request, lambda state, message: output({"state": state, "message": message}))
        output({"state": "complete", **result})
        return 0
    except Exception as error:
        secret = str(request.get("password") or "")
        message = str(error).replace(secret, "[hidden]") if secret else str(error)
        output({"state": "failed", "message": message})
        return 1
    finally:
        if lock is not None:
            os.close(lock)
