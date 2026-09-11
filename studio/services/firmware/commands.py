"""Pure command construction for flashing the one board Studio supports.

Keeping shell construction out of the widget makes the destructive bit easy
to inspect and test. Commands are returned as argument arrays and are never
passed through a shell.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

BOARD_NAME = "ESP32-S3-CAM (N16R8)"
FQBN = "esp32:esp32:esp32s3"
DEFAULT_UPLOAD_SPEED = "921600"
SERIAL_COMMAND_MAX_BYTES = 512

_FIXED_OPTIONS = {
    "USBMode": "hwcdc",
    # This board's labelled UART connector is a CH340 bridge. Keep `Serial`
    # on UART0 so flashing and monitoring use the same cable and port.
    "CDCOnBoot": "default",
    "UploadMode": "default",
    "CPUFreq": "240",
    "FlashMode": "qio",
    "FlashSize": "16M",
    "PartitionScheme": "app3M_fat9M_16MB",
    "PSRAM": "opi",
}


def wifi_provisioning_command(ssid: str, password: str) -> bytes:
    """Encode the firmware's newline-delimited USB provisioning command."""
    _validate_wifi(ssid, password)
    payload = {
        "desk_buddy_command": "set_wifi",
        "ssid": ssid,
        "password": password,
    }
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def _validate_wifi(ssid: str, password: str) -> None:
    ssid_bytes = ssid.encode("utf-8")
    password_bytes = password.encode("utf-8")
    if not 1 <= len(ssid_bytes) <= 32:
        raise ValueError("Wi-Fi name must be between 1 and 32 bytes.")
    valid_password = (
        len(password_bytes) == 0
        or 8 <= len(password_bytes) <= 63
        or (
            len(password_bytes) == 64
            and all(character in b"0123456789abcdefABCDEF" for character in password_bytes)
        )
    )
    if not valid_password:
        raise ValueError(
            "Wi-Fi password must be empty for an open network, 8-63 characters, "
            "or a 64-digit hexadecimal key."
        )


def profile_provisioning_command(
    *,
    wifi_ssid: str,
    wifi_password: str,
    server: str,
    port: int,
    user: str,
    password: str,
    client_id: str,
    tls: bool,
) -> bytes:
    """Encode one atomic Wi-Fi + MQTT serial provisioning command.

    The ESP32 restarts after each legacy provisioning command.  The combined
    command lets Studio update both namespaces and restart exactly once.
    """
    _validate_wifi(wifi_ssid, wifi_password)
    server = server.strip()
    if not server:
        raise ValueError("MQTT server is required.")
    if len(server.encode("utf-8")) > 128:
        raise ValueError("MQTT server must be at most 128 bytes.")
    if not 1 <= int(port) <= 65535:
        raise ValueError("MQTT port must be between 1 and 65535.")
    user = user.strip()
    client_id = client_id.strip()
    if not user:
        raise ValueError("MQTT username is required.")
    if user.lower() == "studio":
        raise ValueError("Studio's account is reserved for Studio and cannot provision a robot.")
    if len(user.encode("utf-8")) > 64:
        raise ValueError("MQTT username must be at most 64 bytes.")
    if not password:
        raise ValueError("MQTT password is required.")
    if len(password.encode("utf-8")) > 128:
        raise ValueError("MQTT password must be at most 128 bytes.")
    if not client_id:
        raise ValueError("MQTT client ID is required.")
    if len(client_id.encode("utf-8")) > 64:
        raise ValueError("MQTT client ID must be at most 64 bytes.")

    payload = {
        "desk_buddy_command": "set_profile",
        "wifi": {"ssid": wifi_ssid, "password": wifi_password},
        "mqtt": {
            "server": server,
            "port": int(port),
            "user": user,
            "password": password,
            "client_id": client_id,
            "tls": bool(tls),
        },
    }
    encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    if len(encoded) > SERIAL_COMMAND_MAX_BYTES:
        raise ValueError(
            f"The connection profile is too large for the ESP32 serial protocol "
            f"({len(encoded)} of {SERIAL_COMMAND_MAX_BYTES} bytes)."
        )
    return encoded


def mqtt_provisioning_command(
    server: str, port: int, user: str = "", password: str = "", client_id: str = "",
    tls: bool | None = None,
) -> bytes:
    """Encode the firmware's newline-delimited USB MQTT provisioning command.

    Every field but `server` is optional: firmware only overwrites a saved
    value when it is sent non-empty (SerialProvisioning.cpp's handleMqtt,
    mirroring the access-point web form's own MQTT section), so leaving user,
    password, or client_id blank here keeps whatever the robot already has.

    `tls` selects the transport. The firmware defaults to TLS, which its
    cloud broker needs — but Studio's own local broker serves plaintext
    (no certificates to manage for a no-root local install), and a TLS
    client against it fails the handshake with an opaque rc=-2. Pass False
    for a Studio-managed broker; None leaves the robot's setting alone.
    """
    server = server.strip()
    if not server:
        raise ValueError("MQTT server is required.")
    if not 0 <= port <= 65535:
        raise ValueError("MQTT port must be between 0 and 65535.")

    payload = {"desk_buddy_command": "set_mqtt", "server": server, "port": port}
    if tls is not None:
        payload["tls"] = tls
    if user:
        payload["user"] = user
    if password:
        payload["password"] = password
    if client_id:
        payload["client_id"] = client_id
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


@dataclass(frozen=True)
class UsbPort:
    address: str
    label: str
    board: str = ""

    @property
    def display(self) -> str:
        details = self.board or self.label
        return f"{self.address} — {details}" if details != self.address else self.address


def _looks_like_usb(address: str, properties: dict) -> bool:
    """Exclude motherboard UARTs while retaining USB serial ports on each OS."""
    lowered = address.lower()
    if properties.get("vid") or properties.get("pid"):
        return True
    if os.name == "nt":
        return lowered.startswith("com")
    markers = ("ttyusb", "ttyacm", "cu.usb", "tty.usb", "cu.slab", "cu.wch")
    return any(marker in lowered for marker in markers)


def parse_usb_ports(raw: str | bytes) -> list[UsbPort]:
    """Read `arduino-cli board list --format json`, tolerating bad tool output."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []

    ports: list[UsbPort] = []
    for detected in payload.get("detected_ports", []):
        port = detected.get("port") or {}
        address = str(port.get("address") or "").strip()
        properties = port.get("properties") or {}
        if not address or not _looks_like_usb(address, properties):
            continue
        boards = detected.get("matching_boards") or []
        board = str(boards[0].get("name") or "") if boards else ""
        ports.append(
            UsbPort(address, str(port.get("label") or address), board)
        )
    return ports


def describe_ports(ports: list[UsbPort]) -> str:
    if not ports:
        return (
            "No USB serial devices found. Connect the ESP32-S3-CAM, hold BOOT "
            "while tapping RESET if needed, then scan again."
        )
    heading = f"Found {len(ports)} USB serial device{'s' if len(ports) != 1 else ''}:"
    return "\n".join([heading, *(f"  {port.display}" for port in ports)])


def board_options(upload_speed: str, erase: bool = False) -> str:
    options = {**_FIXED_OPTIONS, "UploadSpeed": upload_speed}
    options["EraseFlash"] = "all" if erase else "none"
    return ",".join(f"{key}={value}" for key, value in options.items())


def build_command(
    sketch: Path,
    *,
    port: str = "",
    upload_speed: str = DEFAULT_UPLOAD_SPEED,
    erase: bool = False,
    verbose: bool = False,
) -> list[str]:
    """Build an Arduino CLI compile, optionally followed by an upload."""
    command = [
        "compile",
        "--fqbn",
        FQBN,
        "--libraries",
        str(sketch / "vendor"),
        "--board-options",
        board_options(upload_speed, erase),
    ]
    if port:
        command.extend(["--upload", "--port", port])
    if verbose:
        command.append("--verbose")
    command.append(str(sketch))
    return command
