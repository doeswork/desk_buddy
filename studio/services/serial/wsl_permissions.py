"""Selected-device permission helper, executable as a standalone WSL script."""
from __future__ import annotations

import argparse
import os
import re
import stat
from pathlib import Path


def usb_identity(address: str) -> tuple[str, str, str]:
    if not re.fullmatch(r"/dev/tty(?:USB|ACM)\d+", address):
        raise ValueError("Only a USB serial device can be granted access.")
    device = (Path("/sys/class/tty") / Path(address).name / "device").resolve()
    for parent in (device, *device.parents):
        if (parent / "idVendor").is_file() and (parent / "idProduct").is_file():
            serial = parent / "serial"
            return ((parent / "idVendor").read_text().strip().lower(),
                    (parent / "idProduct").read_text().strip().lower(),
                    serial.read_text().strip() if serial.is_file() else "")
    raise ValueError("The selected port has no USB identity in sysfs.")


def grant(address: str, vid: str, pid: str, serial: str, uid: int) -> None:
    if uid <= 0:
        raise ValueError("A non-root Studio user is required.")
    before = os.lstat(address)
    if not stat.S_ISCHR(before.st_mode):
        raise ValueError("The selected path is not a serial device node.")
    actual_vid, actual_pid, actual_serial = usb_identity(address)
    if (actual_vid, actual_pid) != (vid.lower(), pid.lower()) or (serial and serial != actual_serial):
        raise ValueError("USB identity changed; select the device again.")
    # Open the verified node without following a replacement symlink. Perform
    # ownership changes on the descriptor, not on a potentially replaced path.
    fd = os.open(address, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_NOCTTY)
    try:
        current = os.fstat(fd)
        if (current.st_ino, current.st_rdev) != (before.st_ino, before.st_rdev):
            raise ValueError("Serial device changed during permission setup.")
        os.fchown(fd, uid, -1)
        os.fchmod(fd, stat.S_IMODE(current.st_mode) | stat.S_IRUSR | stat.S_IWUSR)
    finally:
        os.close(fd)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("address")
    parser.add_argument("vid")
    parser.add_argument("pid")
    parser.add_argument("serial")
    parser.add_argument("uid", type=int)
    args = parser.parse_args()
    grant(args.address, args.vid, args.pid, args.serial, args.uid)
