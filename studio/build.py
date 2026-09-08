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

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ``python studio/build.py`` also works from the repository root. Importing
# through the package keeps bootstrap generation identical to normal Studio.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from studio.services.vision.bootstrap import UV_VERSION, build_worker_zipapp

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"
DEPLOYMENT = HERE / "deployment"
BUILD_ASSETS = HERE / ".build" / "vision-bootstrap"

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

    problem = prepare_vision_bootstrap()
    if problem:
        print(problem, file=sys.stderr)
        return 1

    # pyside6-deploy installs Nuitka on first run; that download is the slow part.
    deploy_name = "pyside6-deploy.exe" if system == "Windows" else "pyside6-deploy"
    sibling_deploy = Path(sys.executable).absolute().parent / deploy_name
    deploy = shutil.which("pyside6-deploy") or (
        str(sibling_deploy) if sibling_deploy.exists() else ""
    )
    if not deploy:
        print("pyside6-deploy is missing; install studio/requirements.txt", file=sys.stderr)
        return 1
    temporary = tempfile.NamedTemporaryFile(
        dir=HERE, prefix=".pysidedeploy-", suffix=".spec", delete=False,
    )
    temporary_spec = Path(temporary.name)
    temporary.close()
    shutil.copy2(HERE / "pysidedeploy.spec", temporary_spec)
    cmd = [
        deploy,
        str(HERE / "__main__.py"),
        "-c", str(temporary_spec),
        "--force",
    ]
    build_environment = dict(os.environ)
    interpreter_bin = str(Path(sys.executable).absolute().parent)
    build_environment["PATH"] = interpreter_bin + os.pathsep + build_environment.get("PATH", "")
    build_environment["PYTHONPATH"] = str(HERE.parent) + os.pathsep + build_environment.get("PYTHONPATH", "")
    try:
        result = subprocess.run(cmd, cwd=HERE.parent, env=build_environment)
    finally:
        temporary_spec.unlink(missing_ok=True)
    if result.returncode != 0:
        print("Build failed.", file=sys.stderr)
        return result.returncode

    DIST.mkdir(exist_ok=True)
    name = ARTIFACT[system]
    # pyside6-deploy drops the binary next to the spec, named after the app.
    for candidate in (
        HERE / name,
        HERE / "DeskBuddyStudio",
        HERE / "app.bin",
        HERE / "app.exe",
        DEPLOYMENT / name,
        DEPLOYMENT / "DeskBuddyStudio",
        DEPLOYMENT / "app.bin",
        DEPLOYMENT / "app.exe",
        HERE.parent / name,
        HERE.parent / "DeskBuddyStudio",
        HERE.parent / "__main__.bin",
        HERE.parent / "__main__.exe",
    ):
        if candidate.exists():
            target = DIST / name
            if target.exists():
                shutil.rmtree(target) if target.is_dir() else target.unlink()
            shutil.move(str(candidate), str(target))
            problem = self_test_artifact(target, system)
            if problem:
                print(problem, file=sys.stderr)
                return 1
            print(f"\nBuilt: {target}")
            return 0

    print(f"Build reported success but no artifact found in {HERE}", file=sys.stderr)
    print("Files present:", sorted(p.name for p in HERE.iterdir()), file=sys.stderr)
    return 1


def prepare_vision_bootstrap() -> str:
    """Build the pure worker and stage the pinned uv executable for Nuitka."""
    # pyside6-deploy clears ``deployment/`` before invoking Nuitka, so data
    # inputs must live outside its output directory.
    target = BUILD_ASSETS
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    worker = build_worker_zipapp(target / "vision_worker.pyz")
    checked = subprocess.run(
        [sys.executable, str(worker), "self-test"],
        capture_output=True,
        text=True,
    )
    if checked.returncode:
        return "Vision worker self-test failed: " + (checked.stderr or checked.stdout).strip()

    executable_name = "uv.exe" if system_name() == "Windows" else "uv"
    sibling = Path(sys.executable).absolute().parent / executable_name
    uv = shutil.which("uv") or (str(sibling) if sibling.exists() else "")
    if not uv:
        return f"uv {UV_VERSION} is required; install studio/requirements.txt before building"
    version = subprocess.run([uv, "--version"], capture_output=True, text=True)
    if version.returncode or f"uv {UV_VERSION}" not in version.stdout.strip():
        return f"build requires exactly uv {UV_VERSION}; found {version.stdout.strip() or 'unknown'}"
    shutil.copy2(uv, target / executable_name)
    return ""


def system_name() -> str:
    return platform.system()


def self_test_artifact(target: Path, system: str) -> str:
    """Prove the packaged app can find uv and launch its managed worker."""
    if system == "Darwin":
        executable_dir = target / "Contents" / "MacOS"
        candidates = [path for path in executable_dir.iterdir() if path.is_file()]
        if not candidates:
            return f"No executable found inside {target}"
        named = executable_dir / target.stem
        executable = named if named.exists() else candidates[0]
    else:
        executable = target
    try:
        result = subprocess.run(
            [str(executable), "--vision-bootstrap-self-test"],
            capture_output=True,
            text=True,
            timeout=360,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"Packaged Vision bootstrap self-test could not run: {exc}"
    if result.returncode:
        return "Packaged Vision bootstrap self-test failed: " + (
            result.stderr or result.stdout
        ).strip()
    if '"ok": true' not in result.stdout:
        return "Packaged Vision bootstrap self-test returned unexpected output"
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
