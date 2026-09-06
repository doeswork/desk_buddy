"""Qt entry point.

    python -m studio

The package is imported as a package, never run file-by-file — the modules use
relative imports and a bare `python studio/app.py` cannot resolve them.
"""

from __future__ import annotations

import sys


def main() -> int:
    # Dispatch the privileged packaged-build helper before importing Qt or
    # the application UI. Source builds use setup_entry.py for the same narrow
    # boundary.
    if len(sys.argv) == 4 and sys.argv[1] == "--broker-setup-helper":
        from .services.network.broker.setup_helper import main as setup_main
        return setup_main(sys.argv[2], sys.argv[3])

    from PySide6.QtWidgets import QApplication
    from .storage.settings import APP, ORG
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    # These decide where Qt puts the preferences file, so they must not contain
    # spaces and must never change once shipped — a rename orphans every user's
    # existing settings.
    app.setApplicationName(APP)
    app.setOrganizationName(ORG)
    app.setApplicationDisplayName("Desk Buddy Studio")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
