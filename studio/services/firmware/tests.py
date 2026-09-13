"""Firmware discovery and command tests.

    python -m studio.services.firmware.tests
"""

from __future__ import annotations

from pathlib import Path

from .commands import (
    build_command,
    describe_ports,
    parse_usb_ports,
    profile_provisioning_command,
    wifi_provisioning_command,
)


def test_usb_devices_are_kept_and_builtin_uarts_are_ignored() -> None:
    raw = """{
      "detected_ports": [
        {"port": {"address": "/dev/ttyS0", "label": "UART", "properties": {}}},
        {"port": {"address": "/dev/ttyACM0", "label": "USB Serial", "properties": {"vid": "303a"}},
         "matching_boards": [{"name": "ESP32S3 Dev Module"}]}
      ]
    }"""
    ports = parse_usb_ports(raw)
    assert [port.address for port in ports] == ["/dev/ttyACM0"]
    assert "ESP32S3 Dev Module" in ports[0].display


def test_bad_discovery_output_is_an_empty_device_list() -> None:
    assert parse_usb_ports("not json") == []
    assert "No USB serial devices" in describe_ports([])


def test_compile_profile_is_fixed_to_n16r8() -> None:
    command = build_command(Path("/code/firmware"))
    joined = " ".join(command)
    assert "esp32:esp32:esp32s3" in command
    assert "FlashSize=16M" in joined
    assert "PSRAM=opi" in joined
    assert "PartitionScheme=app3M_fat9M_16MB" in joined
    assert "CDCOnBoot=default" in joined
    assert command[command.index("--libraries") + 1] == "/code/firmware/vendor"
    assert "--upload" not in command


def test_flash_adds_port_and_optional_controls() -> None:
    command = build_command(
        Path("/code/firmware"), port="/dev/ttyACM0",
        upload_speed="115200", erase=True, verbose=True,
    )
    joined = " ".join(command)
    assert "--upload" in command
    assert command[command.index("--port") + 1] == "/dev/ttyACM0"
    assert "UploadSpeed=115200" in joined
    assert "EraseFlash=all" in joined
    assert "--verbose" in command


def test_wifi_provisioning_is_json_but_never_needed_for_logging() -> None:
    command = wifi_provisioning_command('Desk "Lab"', "secret123")
    assert command.endswith(b"\n")
    assert b'"desk_buddy_command":"set_wifi"' in command
    assert b'Desk \\"Lab\\"' in command


def test_wifi_provisioning_validates_esp32_limits() -> None:
    for ssid, password in (("", "secret123"), ("x" * 33, "secret123"), ("Desk", "short")):
        try:
            wifi_provisioning_command(ssid, password)
        except ValueError:
            pass
        else:
            raise AssertionError((ssid, password))
    assert wifi_provisioning_command("Open network", "")
    assert wifi_provisioning_command("Desk", "a" * 64)


def test_profile_provisioning_contains_both_namespaces() -> None:
    command = profile_provisioning_command(
        wifi_ssid="Desk Lab",
        wifi_password="secret123",
        server="192.168.1.50",
        port=1883,
        user="robot-1",
        password="mqtt-secret",
        client_id="robot-1",
        tls=False,
    )
    assert command.endswith(b"\n")
    assert b'"desk_buddy_command":"set_profile"' in command
    assert b'"wifi":{"ssid":"Desk Lab"' in command
    assert b'"mqtt":{"server":"192.168.1.50"' in command


def test_profile_provisioning_rejects_missing_fields_and_oversized_payload() -> None:
    arguments = dict(
        wifi_ssid="Desk",
        wifi_password="secret123",
        server="broker.example.com",
        port=1883,
        user="robot-1",
        password="mqtt-secret",
        client_id="robot-1",
        tls=True,
    )
    for key, value in (("user", ""), ("password", ""), ("client_id", "")):
        invalid = {**arguments, key: value}
        try:
            profile_provisioning_command(**invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(key)

    try:
        profile_provisioning_command(**{**arguments, "user": "studio"})
    except ValueError as error:
        assert "reserved" in str(error)
    else:
        raise AssertionError("Studio account was offered for a robot")

    try:
        profile_provisioning_command(**{**arguments, "server": "x" * 400})
    except ValueError as error:
        assert "too large" in str(error) or "at most 128 bytes" in str(error)
    else:
        raise AssertionError("oversized profile was accepted")


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} firmware command tests")
