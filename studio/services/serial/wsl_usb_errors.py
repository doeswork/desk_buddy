"""Readable USB/IP failures, separate from discovery and session coordination."""
from __future__ import annotations


class UsbOperationError(RuntimeError):
    def __init__(self, action: str, output: str):
        self.output = output.strip()
        errors = [line.strip() for line in self.output.splitlines()
                  if "usbipd: error:" in line.lower() or "usbip: error:" in line.lower()]
        detail = "\n".join(errors) or self.output
        if "device in error state" in self.output.lower():
            self.message = "Windows USB could not hand the board to WSL: device in error state."
            self.detail = (
                "Unplug and reconnect the USB cable, then Refresh and choose Use in Studio. "
                "For this firmware, use the board's UART/CH340 connector. "
                "Close any Windows serial monitor that is using the board. "
                "If it still fails, check the device in Windows Device Manager.\n\n" + detail
            )
        else:
            self.message = f"Windows USB could not {action.replace('_', ' ')} the selected device."
            self.detail = detail
        super().__init__(self.message + "\n" + self.detail)
