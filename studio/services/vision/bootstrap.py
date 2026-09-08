"""Materialize the small bootstrap used by the isolated vision runtime."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipapp
from pathlib import Path

from ...storage.store import data_dir

UV_VERSION = "0.12.10"
PYTHON_VERSION = "3.12.14"
RUNTIME_REQUIREMENTS = (
    "paho-mqtt==2.1.0",
    "Pillow==11.3.0",
    "scipy==1.18.1",
    "torch==2.11.0",
    "transformers==4.57.6",
)


def vision_root() -> Path:
    path = data_dir() / "vision"
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def build_worker_zipapp(target: str | Path) -> Path:
    """Create a worker archive from the dependency-free runtime sources."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent / "worker_runtime"
    zipapp.create_archive(
        source,
        target=target,
        compressed=True,
        filter=lambda path: "__pycache__" not in path.parts and path.suffix != ".pyc",
    )
    return target


def bundled_asset_dir() -> Path:
    """Location Nuitka extracts bundled onefile data into."""
    return Path(__file__).resolve().parent / "assets"


def materialize_bootstrap(root: str | Path | None = None) -> tuple[Path, Path]:
    """Return persistent ``(uv, worker.pyz)`` paths for source or packaged Studio."""
    base = Path(root) if root is not None else vision_root()
    destination = base / "bootstrap"
    destination.mkdir(parents=True, exist_ok=True)

    bundled = bundled_asset_dir()
    bundled_worker = bundled / "vision_worker.pyz"
    worker_source = bundled_worker if bundled_worker.exists() else None
    if worker_source is None:
        temporary = destination / ".vision_worker.pyz.tmp"
        build_worker_zipapp(temporary)
        digest = _digest(temporary)
        worker = destination / f"vision-worker-{digest[:12]}.pyz"
        if not worker.exists():
            os.replace(temporary, worker)
        else:
            temporary.unlink(missing_ok=True)
    else:
        digest = _digest(worker_source)
        worker = destination / f"vision-worker-{digest[:12]}.pyz"
        if not worker.exists():
            shutil.copy2(worker_source, worker)

    executable_name = "uv.exe" if os.name == "nt" else "uv"
    bundled_uv = bundled / executable_name
    is_bundled = bundled_uv.exists()
    found_uv = bundled_uv if is_bundled else _find_uv()
    if found_uv is None:
        raise RuntimeError(
            "The Vision installer needs uv. Reinstall Studio or run "
            f"{sys.executable} -m pip install uv=={UV_VERSION}."
        )
    if not is_bundled:
        checked = subprocess.run(
            [str(found_uv), "--version"], capture_output=True, text=True, timeout=10,
        )
        if checked.returncode or f"uv {UV_VERSION}" not in checked.stdout.strip():
            found = checked.stdout.strip() or "unknown"
            raise RuntimeError(f"Vision requires exactly uv {UV_VERSION}; found {found}.")
    uv = destination / executable_name
    if not uv.exists() or _digest(uv) != _digest(found_uv):
        temporary_uv = destination / f".{executable_name}.tmp"
        shutil.copy2(found_uv, temporary_uv)
        os.replace(temporary_uv, uv)
    if os.name != "nt":
        uv.chmod(0o700)
    return uv, worker


def runtime_fingerprint() -> str:
    value = "\n".join((PYTHON_VERSION, *RUNTIME_REQUIREMENTS, f"uv={UV_VERSION}"))
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def bootstrap_self_test(root: str | Path) -> dict:
    """Exercise embedded assets using uv's managed Python, not the host's."""
    base = Path(root)
    uv, worker = materialize_bootstrap(base)
    environment = dict(os.environ)
    environment.update({
        "UV_PYTHON_INSTALL_DIR": str(base / "python"),
        "UV_PYTHON_BIN_DIR": str(base / "python-bin"),
        "UV_CACHE_DIR": str(base / "uv-cache"),
        "UV_PYTHON_INSTALL_REGISTRY": "0",
    })
    result = subprocess.run(
        [
            str(uv), "run", "--isolated", "--no-project",
            "--python", PYTHON_VERSION, "--managed-python",
            str(worker), "self-test",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        env=environment,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or "managed worker self-test failed")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    try:
        worker_result = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError("managed worker self-test returned invalid output") from exc
    if worker_result.get("ok") is not True:
        raise RuntimeError("managed worker self-test did not report success")
    return {
        "ok": True,
        "python": PYTHON_VERSION,
        "uv": UV_VERSION,
        "worker": worker_result.get("worker"),
    }


def _find_uv() -> Path | None:
    found = shutil.which("uv")
    if found:
        return Path(found)
    # Do not resolve the virtualenv's Python symlink: uv is beside that link,
    # not beside the base interpreter it points at.
    candidate = Path(sys.executable).absolute().parent / ("uv.exe" if os.name == "nt" else "uv")
    return candidate if candidate.exists() else None


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
