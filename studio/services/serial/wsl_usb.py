"""USB/IP discovery and session ownership for Studio in WSL. No Qt required."""
from __future__ import annotations

import json
import os
import platform
import queue
import re
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from ..wsl import windows
from .wsl_permissions import usb_identity
from .wsl_usb_errors import UsbOperationError

INSTALL_URL = "https://learn.microsoft.com/en-us/windows/wsl/connect-usb"
RETRY_DELAYS = (1, 2, 5, 10)


@dataclass(frozen=True)
class LinuxPort:
    address: str
    vid: str
    pid: str
    serial: str = ""
    node_id: tuple[int, int] = ()


@dataclass(frozen=True)
class UsbDevice:
    instance_id: str
    description: str
    busid: str = ""
    persisted_guid: str = ""
    vid: str = ""
    pid: str = ""
    serial: str = ""
    com_port: str = ""
    attached: bool = False
    linux_port: str = ""

    @property
    def identity(self) -> str:
        return self.persisted_guid or self.instance_id

    @property
    def shared(self) -> bool:
        return bool(self.persisted_guid)

    @property
    def candidate(self) -> bool:
        return bool(self.com_port or self.vid in {"1a86", "10c4", "303a", "0403"}
                    or re.search(r"ESP32|CH34|CH91|CP210|USB.?SERIAL", self.description, re.I))


@dataclass(frozen=True)
class UsbAccessStatus:
    state: str = "idle"
    message: str = "Check Windows USB devices to get started."
    devices: tuple[UsbDevice, ...] = ()
    selected: UsbDevice | None = None
    ports: tuple[LinuxPort, ...] = ()
    tool: str = ""
    version: str = ""
    winget: str = ""
    windows_temp: str = ""
    service_running: bool = False
    detail: str = ""

    @property
    def supported(self) -> bool:
        match = re.search(r"\b(\d+)\.", self.version)
        return bool(self.tool and match and int(match[1]) >= 5)


class SelectionRequired(RuntimeError):
    pass


def same_device(left: UsbDevice, right: UsbDevice) -> bool:
    return bool((left.persisted_guid and left.persisted_guid == right.persisted_guid)
                or (left.instance_id and left.instance_id.casefold() == right.instance_id.casefold()))


def parse_devices(state: dict, serial_ports: list[dict]) -> tuple[UsbDevice, ...]:
    raw_devices = state.get("Devices", [])
    if not isinstance(raw_devices, list):
        raise ValueError("usbipd returned an unsupported device list. Update USB support.")
    devices = []
    for raw in raw_devices:
        instance = str(raw.get("InstanceId") or "")
        if not instance:
            continue
        hardware = re.search(r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", instance, re.I)
        vid, pid = (value.lower() for value in hardware.groups()) if hardware else ("", "")
        tail = instance.rsplit("\\", 1)[-1]
        serial = tail if "&" not in tail and "\\" in instance else ""
        com = next((str(port.get("DeviceID") or "") for port in serial_ports
                    if instance.casefold() in [str(value).casefold() for value in
                        (port.get("Ancestors") or [port.get("PNPDeviceID", "")])]), "")
        devices.append(UsbDevice(instance, str(raw.get("Description") or "USB device"),
                                 str(raw.get("BusId") or ""), str(raw.get("PersistedGuid") or ""),
                                 vid, pid, serial, com, bool(raw.get("ClientIPAddress"))))
    return tuple(devices)


def matching_ports(device: UsbDevice, ports: tuple[LinuxPort, ...]) -> tuple[LinuxPort, ...]:
    return tuple(port for port in ports if (port.vid, port.pid) == (device.vid, device.pid)
                 and (not device.serial or port.serial.casefold() == device.serial.casefold()))


INSPECT_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    $command = Get-Command usbipd.exe -ErrorAction SilentlyContinue
    $tool = if ($command) { $command.Source } else { Join-Path $env:ProgramFiles 'usbipd-win\usbipd.exe' }
    if (-not (Test-Path -LiteralPath $tool)) { $tool = '' }
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    $ports = @(Get-CimInstance Win32_SerialPort | ForEach-Object {
        $id = $_.PNPDeviceID; $ancestors = @($id)
        for ($i=0; $i -lt 4; $i++) {
            $parent = Get-PnpDeviceProperty -InstanceId $id -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue
            if (-not $parent.Data) { break }
            $id = [string]$parent.Data; $ancestors += $id
        }
        @{ DeviceID=$_.DeviceID; PNPDeviceID=$_.PNPDeviceID; Name=$_.Name; Ancestors=$ancestors }
    })
    $version = ''; $state = @{ Devices=@() }
    if ($tool) {
        $version = (& $tool --version | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Cannot query usbipd version.' }
        if ($version -match '\b([0-9]+)\.' -and [int]$Matches[1] -ge 5) {
            $raw = & $tool state | Out-String
            if ($LASTEXITCODE -ne 0) { throw 'Cannot query usbipd state. Check the USBIP Device Host service.' }
            $state = $raw | ConvertFrom-Json
        }
    }
    $service = Get-Service usbipd -ErrorAction SilentlyContinue
    @{ tool=$tool; version=$version; state=$state; serialPorts=$ports;
       winget=$(if ($winget) {$winget.Source} else {''}); temp=$env:TEMP;
       serviceRunning=($service -and $service.Status -eq 'Running') } | ConvertTo-Json -Depth 8 -Compress
} catch { @{ error=$_.Exception.Message } | ConvertTo-Json -Compress }
"""


class WindowsUsbBackend:
    def __init__(self, distribution: str | None = None):
        self.distribution = distribution if distribution is not None else os.getenv("WSL_DISTRO_NAME", "")

    def check_environment(self):
        if not windows.is_wsl() or "wsl2" not in platform.release().lower():
            raise RuntimeError("Windows USB access requires WSL2. Native serial access is still available.")
        version = re.match(r"(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?", platform.release())
        if not version or tuple(int(value or 0) for value in version.groups()) < (5, 10, 60, 1):
            raise RuntimeError("Update the WSL kernel using Windows settings, then restart Studio.")
        if not self.distribution:
            raise RuntimeError("WSL_DISTRO_NAME is missing. Launch Studio from your WSL distribution.")

    def linux_ports(self) -> tuple[LinuxPort, ...]:
        ports = []
        for pattern in ("ttyUSB*", "ttyACM*"):
            for node in sorted(Path("/dev").glob(pattern)):
                try:
                    stat = node.stat()
                    ports.append(LinuxPort(str(node), *usb_identity(str(node)), (stat.st_ino, stat.st_rdev)))
                except (OSError, ValueError):
                    continue
        return tuple(ports)

    def inspect(self) -> UsbAccessStatus:
        self.check_environment()
        try:
            data = windows.decode(windows.powershell(INSPECT_SCRIPT))
        except OSError as error:
            raise RuntimeError("Cannot start Windows PowerShell. Enable Windows interoperability in WSL, then retry. " + str(error)) from error
        devices = parse_devices(data.get("state") or {}, data.get("serialPorts") or [])
        if not UsbAccessStatus(tool=data.get("tool", ""), version=data.get("version", "")).supported:
            # Windows COM discovery remains useful before USB/IP is installed.
            raw = []
            for port in data.get("serialPorts", []):
                ancestors = port.get("Ancestors") or [port.get("PNPDeviceID", "")]
                instance = next((s for s in ancestors if re.match(r"USB\\VID_", s, re.I)
                                 and "&MI_" not in s.upper()), port.get("PNPDeviceID", ""))
                raw.append({"InstanceId": instance, "Description": port.get("Name")})
            devices = parse_devices({"Devices": raw}, data.get("serialPorts", []))
        ports = self.linux_ports()
        devices = tuple(replace(d, linux_port=matches[0].address)
                        if d.attached and len(matches := matching_ports(d, ports)) == 1 else d
                        for d in devices)
        status = UsbAccessStatus(devices=devices, tool=str(data.get("tool") or ""),
                                 version=str(data.get("version") or ""), winget=str(data.get("winget") or ""),
                                 windows_temp=str(data.get("temp") or ""), service_running=bool(data.get("serviceRunning")))
        if not status.supported:
            return replace(status, state="needs_install", message="Install or update Windows USB support (usbipd-win 5 or newer).")
        return replace(status, state="available", message="Select a device to use in Studio." if devices else "Windows reports no USB devices. Check the cable and connector, then refresh.")

    def install(self, status: UsbAccessStatus):
        if not status.winget:
            raise RuntimeError("Windows Package Manager (winget) is unavailable. Use the official USB support installation guide.")
        # Invoke winget in the signed-in user's context. Its interactive installer
        # owns UAC and any restart decision; do not elevate the app or auto-reboot.
        script = "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; " + (
            f"& {windows.quote(status.winget)} install --interactive --exact dorssel.usbipd-win --accept-source-agreements; "
            "$code=$LASTEXITCODE; @{ exitCode=$code } | ConvertTo-Json -Compress"
        )
        result = windows.powershell(script, timeout=900)
        # winget writes progress before our result; check the final JSON line.
        lines = result.stdout.strip().splitlines()
        try:
            code = json.loads(lines[-1]).get("exitCode")
        except (ValueError, IndexError):
            raise RuntimeError("The USB installer returned no result. Refresh to check installation.")
        if result.returncode or code != 0:
            # An already installed version can also produce a nonzero exit.
            # Verify the actual installation instead of relying on winget codes.
            if not self.inspect().supported:
                raise RuntimeError(f"USB support installation did not complete (code {code}). Refresh before retrying.")

    def tool_action(self, status: UsbAccessStatus, device: UsbDevice, action: str, *, elevated=False):
        if action not in {"attach", "bind", "detach", "start_service"}:
            raise ValueError("Unsupported USB operation.")
        if not re.fullmatch(r"\d+-\d+(?:\.\d+)*", device.busid):
            raise SelectionRequired("The device is unplugged or its BUSID changed. Refresh and select it again.")
        args = f"{action} --busid {windows.quote(device.busid)}"
        if action == "attach":
            # USB devices are shared by the WSL2 VM; usbipd selects a distro.
            args += " --wsl"
        # usbipd emits informational messages on stderr even on success.
        # PowerShell 5 converts redirected stderr into ErrorRecords; Stop would
        # abort a successful attach on its first progress message.
        command = ("$ErrorActionPreference='Continue'; "
                   f"$output = & {windows.quote(status.tool)} {args} 2>&1 "
                   "| ForEach-Object { $_.ToString() } | Out-String; "
                   "$code=$LASTEXITCODE; $ErrorActionPreference='Stop'; "
                   "if ($code -ne 0) { throw $output };")
        if action == "start_service":
            command = ""
        script = ("$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; try { "
                  + ("Start-Service usbipd; " if elevated else "") + command
                  + " @{ ok=$true } | ConvertTo-Json -Compress } catch { @{ error=$_.Exception.Message } | ConvertTo-Json -Compress }")
        try:
            if elevated:
                windows.elevated(script, status.windows_temp, timeout=180)
            else:
                windows.decode(windows.powershell(script, timeout=45))
        except windows.AuthorizationCancelled:
            raise
        except RuntimeError as error:
            raise UsbOperationError(action, str(error)) from error

    def grant_access(self, port: LinuxPort):
        if os.access(port.address, os.R_OK | os.W_OK):
            return
        wsl = windows.executable("wsl.exe")
        if not wsl:
            raise RuntimeError("Cannot grant serial access: Windows WSL interoperability is unavailable.")
        helper = Path(__file__).with_name("wsl_permissions.py")
        result = windows.run([wsl, "--distribution", self.distribution, "--user", "root", "--exec",
                              "python3", str(helper), port.address, port.vid, port.pid, port.serial, str(os.getuid())])
        if result.returncode or not os.access(port.address, os.R_OK | os.W_OK):
            raise RuntimeError("Could not grant access to the selected serial node: " + result.stderr.strip())


class UsbCoordinator:
    """One serialized host operation, with attachment ownership scoped to this app."""
    def __init__(self, backend=None):
        self.backend = backend or WindowsUsbBackend()
        self.status = UsbAccessStatus()
        self.events: queue.Queue = queue.Queue()
        self.running = False
        self.session_device: UsbDevice | None = None
        self.owned: dict[str, UsbDevice] = {}
        self.recovery_enabled = False
        self.flash_active = False
        self._cancel = threading.Event()
        self._closing = False
        self._thread: threading.Thread | None = None
        self._next_retry = 0.0
        self._retry = 0
        self._verified_port: LinuxPort | None = None
        self._before_addresses: set[str] | None = None

    def _progress(self, state, message):
        self.events.put(("progress", state, message))

    @property
    def verified_port(self):
        return self._verified_port

    @staticmethod
    def _find(status, device):
        matches = [candidate for candidate in status.devices if same_device(candidate, device)]
        if len(matches) != 1:
            raise SelectionRequired("The selected USB identity is missing or ambiguous. Refresh and select the board.")
        return matches[0]

    def _check_cancelled(self):
        if self._cancel.is_set():
            raise windows.AuthorizationCancelled("USB setup cancelled. Refresh before retrying; completed setup steps may remain.")

    def _attach(self, selected, chosen_port="", *, recovery=False):
        self._verified_port = None
        status = self.backend.inspect()
        if not status.supported:
            raise SelectionRequired("Install or update Windows USB support first.")
        if not any(same_device(d, selected) for d in status.devices):
            self.session_device = selected
            if any((d.vid, d.pid) == (selected.vid, selected.pid) for d in status.devices):
                raise SelectionRequired("A board with a different USB identity appeared. Release the previous selection and select the board again.")
            # Do not substitute a different board, even with the same USB IDs.
            return replace(status, state="waiting", selected=selected,
                           message="Waiting for the selected USB identity. If the board's identity changed, release it and select again.")
        selected = self._find(status, selected)
        self.session_device = selected
        if not selected.busid:
            return replace(status, state="waiting", selected=selected, message="Waiting for the selected board to be plugged in…")
        before = self.backend.linux_ports()
        self._check_cancelled()
        if not selected.shared or not status.service_running:
            if recovery:
                raise SelectionRequired("USB sharing or its service changed. Choose Use in Studio to prepare it again.")
            self._progress("sharing", "Preparing the selected Windows USB device; Windows may ask for administrator approval…")
            action = "bind" if not selected.shared else "start_service"
            self.backend.tool_action(status, selected, action, elevated=True)
            self._check_cancelled()
            status = self.backend.inspect()
            selected = self._find(status, selected)
        was_attached = selected.attached
        self._check_cancelled()
        if not was_attached:
            self._before_addresses = {p.address for p in before}
            self._progress("attaching", "Attaching the selected device to WSL…")
            # Record ownership before starting: a timeout may occur after Windows
            # completed the attach. Cleanup always re-inspects the actual device.
            self.owned[selected.identity] = selected
            self.backend.tool_action(status, selected, "attach")
            selected = replace(selected, attached=True)
        self.session_device = selected
        self._check_cancelled()
        self._progress("verifying", "Waiting for the Linux serial port…")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self._check_cancelled()
            matches = matching_ports(selected, self.backend.linux_ports())
            if chosen_port:
                matches = tuple(p for p in matches if p.address == chosen_port)
            elif not selected.serial and self._before_addresses is not None:
                matches = tuple(p for p in matches if p.address not in self._before_addresses)
            if len(matches) > 1:
                return replace(status, state="choose_port", selected=selected, ports=matches,
                               message="Several Linux ports match. Select the board's Linux port to continue.")
            if len(matches) == 1:
                self._check_cancelled()
                self._progress("permissions", "Checking access to the selected serial port…")
                self.backend.grant_access(matches[0])
                self._check_cancelled()
                selected = replace(selected, attached=True, linux_port=matches[0].address)
                self.session_device = selected
                self._verified_port = matches[0]
                self._before_addresses = None
                return replace(status, state="ready", selected=selected, ports=matches,
                               message=f"Ready in Studio: {matches[0].address}")
            self._cancel.wait(0.25)
        return replace(status, state="waiting", selected=selected,
                       message="USB is attached, but no matching Linux serial port appeared.",
                       detail="Check the UART connector and WSL USB driver support. Refresh after correcting the connection.")

    def _release(self, selected, *, cleanup=False):
        status = self.backend.inspect()
        if not cleanup:
            self._check_cancelled()
        matches = [d for d in status.devices if same_device(d, selected)]
        if len(matches) > 1:
            raise SelectionRequired("USB identity is ambiguous. Select the device before releasing it.")
        device = matches[0] if matches else None
        if device and device.attached and device.busid:
            self.backend.tool_action(status, device, "detach")
        self.owned = {key: value for key, value in self.owned.items() if not same_device(value, selected)}
        self.session_device = None
        self._verified_port = None
        self._before_addresses = None
        return replace(self.backend.inspect(), state="released", message="Device released to Windows.")

    def start(self, action: str, device: UsbDevice | None = None, linux_port="", *, recovery=False) -> bool:
        if action not in {"inspect", "install", "attach", "release"}:
            raise ValueError("Unsupported USB operation.")
        if self.running or self._closing or (self.flash_active and action != "inspect" and not recovery):
            return False
        if action in {"attach", "release"} and device is None:
            return False
        if action == "release":
            self.recovery_enabled = False
        elif action == "attach" and not recovery:
            if self.session_device and not same_device(self.session_device, device):
                self.status = replace(self.status, state="selection_required", message="Release the current board before selecting another.")
                return False
            self.recovery_enabled = True
        self.running = True
        self._cancel.clear()
        self.status = replace(self.status, state="checking", message="Checking Windows USB…", detail="")

        def work():
            try:
                if action == "inspect":
                    result = self.backend.inspect()
                elif action == "install":
                    status = self.backend.inspect()
                    self._check_cancelled()
                    self._progress("installing", "Installing Windows USB support. Complete the Windows installer and any administrator prompt…")
                    self.backend.install(status)
                    self._check_cancelled()
                    result = self.backend.inspect()
                elif action == "attach":
                    result = self._attach(device, linux_port, recovery=recovery)
                else:
                    result = self._release(device)
            except windows.AuthorizationCancelled as error:
                self.recovery_enabled = False
                result = replace(self.status, state="cancelled", message=str(error))
            except SelectionRequired as error:
                self.recovery_enabled = False
                result = replace(self.status, state="selection_required", message=str(error))
            except UsbOperationError as error:
                # Binding/attachment can change enumeration even when it fails.
                # Do not keep displaying a stale BUSID or an old sharing state.
                current = self.status
                try:
                    current = self.backend.inspect()
                    matches = [d for d in current.devices if device and same_device(d, device)]
                    if len(matches) == 1:
                        self.session_device = matches[0]
                except Exception:
                    pass  # Preserve the original command failure if refresh fails.
                result = replace(current, state="waiting" if recovery else "failed",
                                 selected=self.session_device, message=error.message, detail=error.detail)
                if not recovery:
                    self.recovery_enabled = False
            except Exception as error:
                result = replace(self.status, state="waiting" if recovery else "failed", message="Windows USB needs attention.", detail=str(error))
                if not recovery:
                    self.recovery_enabled = False
            if result.state == "choose_port":
                self.recovery_enabled = False
            self.events.put(("result", result))
            if self._closing:
                self._cleanup_owned()

        # Finish attachment cleanup on normal process exit. A Windows installer
        # can outlive Studio; cancellation prevents it proceeding to attachment.
        self._thread = threading.Thread(target=work, name="wsl-usb-access", daemon=(action == "install"))
        self._thread.start()
        return True

    def cancel(self):
        self.recovery_enabled = False
        self._cancel.set()
        if not self.running:
            self.status = replace(self.status, state="cancelled", message="USB recovery paused. Use in Studio to resume, or release to Windows.")

    def poll(self) -> bool:
        changed = False
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            changed = True
            if event[0] == "progress":
                self.status = replace(self.status, state=event[1], message=event[2])
            else:
                self.status = event[1]
                self.running = False
                if self.status.state == "ready":
                    self._retry = 0
                delay = RETRY_DELAYS[min(self._retry, len(RETRY_DELAYS) - 1)]
                self._retry += 1
                self._next_retry = time.monotonic() + delay
        if (not self.running and not self._closing and self.recovery_enabled and self.session_device
                and time.monotonic() >= self._next_retry):
            self._next_retry = time.monotonic() + 1
            # Compare the node and USB identity too: a tty path can be reused by
            # another board, or replaced between two timer ticks.
            ports = self.backend.linux_ports()
            if not self._verified_port or self._verified_port not in ports:
                self._verified_port = None
                self.start("attach", self.session_device, recovery=True)
                changed = True
        return changed

    def _cleanup_owned(self):
        for device in tuple(self.owned.values()):
            try:
                self._release(device, cleanup=True)
            except Exception as error:
                print(f"[Studio WSL USB] Could not release {device.description}: {error}", flush=True)

    def shutdown(self):
        self._closing = True
        self.cancel()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
            # An already elevated installer cannot be killed safely by WSL.
            # It owns completion; cancellation prevents subsequent attach steps.
            if self._thread.is_alive():
                return
        if self.owned:
            thread = threading.Thread(target=self._cleanup_owned, name="wsl-usb-release")
            thread.start()
            thread.join(timeout=5)
