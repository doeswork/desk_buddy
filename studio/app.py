"""Qt entry point.

    python -m studio
    python studio/app.py
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

try:
    from .ui.main_window import MainWindow
except ImportError:  # direct script execution
    from ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Desk Buddy Studio")
    app.setOrganizationName("Desk Buddy")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
