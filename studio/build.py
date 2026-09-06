"""Build a native executable for the platform this script runs on.

    python build.py

Windows -> dist/DeskBuddyStudio.exe
macOS   -> dist/DeskBuddyStudio.app
Linux   -> dist/DeskBuddyStudio.bin

There is no cross-compiling. A Windows .exe must be built on Windows, a macOS
.app on macOS. That is what the GitHub Actions matrix in
.github/workflows/build.yml is for.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"
DEPLOYMENT = HERE / "deployment"

ARTIFACT = {
    "Windows": "DeskBuddyStudio.exe",
    "Darwin": "DeskBuddyStudio.app",
    "Linux": "DeskBuddyStudio.bin",
}


def main() -> int:
    system = platform.system()
    if system not in ARTIFACT:
        print(f"Unsupported platform: {system}", file=sys.stderr)
        return 1

    print(f"Building for {system} ({platform.machine()})")

    # pyside6-deploy installs Nuitka on first run; that download is the slow part.
    cmd = [
        sys.executable, "-m", "PySide6.scripts.deploy",
        "-c", str(HERE / "pysidedeploy.spec"),
        "--force",
    ]
    result = subprocess.run(cmd, cwd=HERE)
    if result.returncode != 0:
        print("Build failed.", file=sys.stderr)
        return result.returncode

    DIST.mkdir(exist_ok=True)
    name = ARTIFACT[system]
    # pyside6-deploy drops the binary next to the spec, named after the app.
    for candidate in (HERE / name, HERE / "DeskBuddyStudio", HERE / "app.bin", HERE / "app.exe"):
        if candidate.exists():
            target = DIST / name
            if target.exists():
                shutil.rmtree(target) if target.is_dir() else target.unlink()
            shutil.move(str(candidate), str(target))
            print(f"\nBuilt: {target}")
            return 0

    print(f"Build reported success but no artifact found in {HERE}", file=sys.stderr)
    print("Files present:", sorted(p.name for p in HERE.iterdir()), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
