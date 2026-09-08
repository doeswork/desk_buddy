"""Platform discovery and commands shared by setup and its privileged helper.

This module deliberately uses only the standard library. Importing it never
changes the machine, and the helper can run before QApplication is created.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


def executable(name: str) -> str:
    return shutil.which(name) or next(
        (str(p) for base in ("/usr/sbin", "/sbin", "/usr/bin", "/bin")
         if (p := Path(base) / name).is_file() and os.access(p, os.X_OK)), ""
    )


@dataclass(frozen=True)
class Environment:
    linux: bool
    distro: str
    package_manager: str
    service_manager: str
    wsl: bool
    distribution: str


def detect() -> Environment:
    try:
        release = platform.freedesktop_os_release()
    except OSError:
        release = {}
    managers = ("apt-get", "pacman", "dnf", "zypper", "apk")
    package = next((name for name in managers if executable(name)), "")
    service = ""
    if Path("/run/systemd/system").is_dir() and executable("systemctl"):
        service = "systemd"
    elif executable("rc-service"):
        service = "openrc"
    elif executable("service") and Path("/etc/init.d/mosquitto").exists():
        service = "sysv"
    wsl = "microsoft" in platform.release().lower() or bool(os.getenv("WSL_DISTRO_NAME"))
    return Environment(platform.system() == "Linux", release.get("ID", ""),
                       package, service, wsl, os.getenv("WSL_DISTRO_NAME", ""))


def install_commands(manager: str) -> list[list[str]]:
    commands = {
        "apt-get": [["apt-get", "update"], ["apt-get", "install", "-y", "mosquitto", "mosquitto-clients"]],
        # Do not refresh only the Arch package database (a partial upgrade).
        "pacman": [["pacman", "-S", "--needed", "--noconfirm", "mosquitto"]],
        "dnf": [["dnf", "install", "-y", "mosquitto"]],
        "zypper": [["zypper", "--non-interactive", "install", "mosquitto"]],
        "apk": [["apk", "add", "mosquitto", "mosquitto-clients", "mosquitto-openrc"]],
    }
    if manager not in commands:
        raise RuntimeError("This package manager is not supported. Open Advanced / Manual setup.")
    return commands[manager]


def service_commands(manager: str, action: str) -> list[list[str]]:
    if manager == "systemd":
        return [["systemctl", "enable", "mosquitto"], ["systemctl", "restart", "mosquitto"]] if action == "start" else [["systemctl", action, "mosquitto"]]
    if manager == "openrc":
        return [["rc-update", "add", "mosquitto", "default"], ["rc-service", "mosquitto", "restart"]] if action == "start" else [["rc-service", "mosquitto", action]]
    if manager == "sysv":
        enable = []
        if executable("update-rc.d"):
            enable = [["update-rc.d", "mosquitto", "defaults"]]
        elif executable("chkconfig"):
            enable = [["chkconfig", "mosquitto", "on"]]
        return enable + [["service", "mosquitto", "restart"]] if action == "start" else [["service", "mosquitto", action]]
    raise RuntimeError("No supported service manager is running. Enable systemd or use an installed OpenRC/SysV service.")


def run(command: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess:
    """Never echo argv: account utilities may receive a generated secret."""
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def firewalld_zone() -> str:
    """Use the LAN interface's assigned zone, then firewalld's default zone."""
    import json
    try:
        route = json.loads(run(["ip", "-j", "route", "get", "1.1.1.1"]).stdout)[0]
        result = run(["firewall-cmd", "--get-zone-of-interface", route["dev"]])
        zone = result.stdout.strip()
        if result.returncode == 0 and zone and zone != "no zone":
            return zone
    except (OSError, ValueError, KeyError, IndexError):
        pass
    result = run(["firewall-cmd", "--get-default-zone"])
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("The LAN firewall zone could not be determined.")
    return result.stdout.strip()
