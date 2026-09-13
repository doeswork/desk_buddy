"""User-local Arduino CLI bootstrap; compiler packages are installed by the CLI."""
from __future__ import annotations

import json
import os
import platform
import shutil
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

CLI_VERSION = "1.5.1"
ESP32_INDEX = "https://espressif.github.io/arduino-esp32/package_esp32_index.json"


def managed_cli() -> Path:
    from ...storage.store import data_dir
    return data_dir() / "firmware-tools" / ("arduino-cli.exe" if os.name == "nt" else "arduino-cli")


def find_cli() -> str:
    return shutil.which("arduino-cli") or (str(managed_cli()) if managed_cli().is_file() else "")


def archive_name(system=None, machine=None) -> str:
    system, machine = system or platform.system(), (machine or platform.machine()).lower()
    arch = {"x86_64": "64bit", "amd64": "64bit", "aarch64": "ARM64", "arm64": "ARM64",
            "i386": "32bit", "i686": "32bit", "armv7l": "ARMv7", "armv6l": "ARMv6"}.get(machine)
    if system == "Windows" and arch == "ARM64":
        arch = "64bit"  # Windows 11 supports x64 executables on ARM.
    supported = {"Linux": {"64bit", "32bit", "ARM64", "ARMv7", "ARMv6"},
                 "Darwin": {"64bit", "ARM64"}, "Windows": {"64bit", "32bit"}}
    if arch not in supported.get(system, set()):
        raise RuntimeError(f"Arduino CLI automatic installation does not support {system} {machine}.")
    suffix = "zip" if system == "Windows" else "tar.gz"
    return f"arduino-cli_{CLI_VERSION}_{'macOS' if system == 'Darwin' else system}_{arch}.{suffix}"


def has_esp32(raw: bytes) -> bool:
    data = json.loads(raw)
    platforms = data if isinstance(data, list) else data.get("platforms", [])
    return any(p.get("id") == "esp32:esp32" and (p.get("installed") or p.get("installed_version"))
               for p in platforms)


def install_cli(destination: Path, cancelled, progress=lambda message: None) -> str:
    """Download to staging and publish only a complete executable. Runs off Qt."""
    name = archive_name()
    url = f"https://downloads.arduino.cc/arduino-cli/{name}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 300
    def check():
        if cancelled.is_set():
            raise RuntimeError("Firmware tool installation cancelled.")
        if time.monotonic() > deadline:
            raise TimeoutError("Arduino CLI download timed out. Try setup again.")
    with tempfile.TemporaryDirectory(prefix=".arduino-", dir=destination.parent) as folder:
        check()
        archive = Path(folder) / name
        progress(f"Downloading Arduino CLI {CLI_VERSION}…")
        with urllib.request.urlopen(url, timeout=20) as response, archive.open("wb") as output:
            while True:
                check()
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                output.write(chunk)
        check()
        executable = "arduino-cli.exe" if name.endswith(".zip") else "arduino-cli"
        # Extract only the executable, never archive-supplied paths.
        staged = Path(folder) / executable
        if name.endswith(".zip"):
            with zipfile.ZipFile(archive) as package:
                with package.open(executable) as source, staged.open("wb") as output:
                    shutil.copyfileobj(source, output)
        else:
            with tarfile.open(archive) as package:
                member = package.getmember(executable)
                if not member.isfile():
                    raise RuntimeError("Arduino download did not contain an executable.")
                with package.extractfile(member) as source, staged.open("wb") as output:
                    shutil.copyfileobj(source, output)
        check()
        staged.chmod(0o755)
        os.replace(staged, destination)
    return str(destination)
