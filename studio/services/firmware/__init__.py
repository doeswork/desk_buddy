"""Commands and discovery data for the supported Desk Buddy firmware."""

from .commands import (
    BOARD_NAME,
    DEFAULT_UPLOAD_SPEED,
    build_command,
    describe_ports,
    mqtt_provisioning_command,
    parse_usb_ports,
    profile_provisioning_command,
    wifi_provisioning_command,
)

__all__ = [
    "BOARD_NAME",
    "DEFAULT_UPLOAD_SPEED",
    "build_command",
    "describe_ports",
    "mqtt_provisioning_command",
    "parse_usb_ports",
    "profile_provisioning_command",
    "wifi_provisioning_command",
]
