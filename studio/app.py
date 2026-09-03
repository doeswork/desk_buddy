"""Qt entry point.

    python -m studio

The package is imported as a package, never run file-by-file — the modules use
relative imports and a bare `python studio/app.py` cannot resolve them.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .storage.user_config.settings import APP, ORG
from .ui.main_window import MainWindow


def main() -> int:
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
