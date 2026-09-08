"""Qt entry point.

    python -m studio

The package is imported as a package, never run file-by-file — the modules use
relative imports and a bare `python studio/app.py` cannot resolve them.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
from pathlib import Path

RESTART_WAIT_ARGUMENT = "--wait-for-previous-instance"
RESTART_WAIT_MS = 30_000


def _lock_path() -> Path:
    """A per-user gate that keeps two Studio processes from running."""
    user = str(os.getuid()) if hasattr(os, "getuid") else "user"
    return Path(tempfile.gettempdir()) / f"desk-buddy-studio-{user}.lock"


def _prepare_wslg() -> None:
    """Point Qt at WSLg's real Wayland socket when the shell lost that path."""
    if os.getenv("QT_QPA_PLATFORM"):
        return
    if "microsoft" not in platform.release().lower() and not os.getenv("WSL_DISTRO_NAME"):
        return
    display = os.getenv("WAYLAND_DISPLAY")
    current = Path(os.getenv("XDG_RUNTIME_DIR", "/run/user/0"))
    wslg = Path("/mnt/wslg/runtime-dir")
    if display and not (current / display).exists() and (wslg / display).exists():
        os.environ["XDG_RUNTIME_DIR"] = str(wslg)
        print(
            f"[Studio] Using WSLg Wayland runtime {wslg}",
            file=sys.stderr,
            flush=True,
        )


def main() -> int:
    # Dispatch the privileged packaged-build helper before importing Qt or
    # the application UI. Source builds use setup_entry.py for the same narrow
    # boundary.
    if len(sys.argv) == 4 and sys.argv[1] == "--broker-setup-helper":
        from .services.network.broker.setup_helper import main as setup_main
        return setup_main(sys.argv[2], sys.argv[3])

    if len(sys.argv) == 2 and sys.argv[1] == "--vision-bootstrap-self-test":
        from .services.vision.bootstrap import bootstrap_self_test
        try:
            with tempfile.TemporaryDirectory(prefix="desk-buddy-vision-test-") as directory:
                report = bootstrap_self_test(directory)
        except Exception as exc:
            print(f"Vision bootstrap self-test failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(report, sort_keys=True))
        return 0

    wait_for_previous = RESTART_WAIT_ARGUMENT in sys.argv[1:]
    if wait_for_previous:
        sys.argv.remove(RESTART_WAIT_ARGUMENT)

    _prepare_wslg()
    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QApplication, QMessageBox
    from .storage.settings import APP, ORG
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    # These decide where Qt puts the preferences file, so they must not contain
    # spaces and must never change once shipped — a rename orphans every user's
    # existing settings.
    app.setApplicationName(APP)
    app.setOrganizationName(ORG)
    app.setApplicationDisplayName("Desk Buddy Studio")

    instance_lock = QLockFile(str(_lock_path()))
    instance_lock.setStaleLockTime(RESTART_WAIT_MS)
    timeout = RESTART_WAIT_MS if wait_for_previous else 0
    if not instance_lock.tryLock(timeout):
        QMessageBox.information(
            None,
            "Desk Buddy Studio is already running",
            "Use the existing Studio window instead of opening another copy.",
        )
        return 0

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
