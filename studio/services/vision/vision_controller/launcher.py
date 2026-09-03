"""Optional local process launcher; worker data still travels only through MQTT."""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LocalWorker:
    worker_id: str
    command: tuple[str, ...]
    cwd: str | None = None
    install_command: tuple[str, ...] = ()


class LocalWorkerLauncher:
    def __init__(self, workers: tuple[LocalWorker, ...] = ()) -> None:
        self.workers = {worker.worker_id: worker for worker in workers}
        self._processes: dict[str, subprocess.Popen] = {}

    @classmethod
    def from_toml(cls, path: str | Path) -> "LocalWorkerLauncher":
        source = Path(path).expanduser().resolve()
        with source.open("rb") as handle:
            raw = tomllib.load(handle)
        workers = []
        for item in raw.get("workers", []):
            command = item.get("command", [])
            if not isinstance(command, list) or not command or not all(isinstance(value, str) and value for value in command):
                raise ValueError("local worker commands must be non-empty string arrays")
            cwd = item.get("cwd")
            if cwd and not Path(str(cwd)).is_absolute():
                cwd = str((source.parent / str(cwd)).resolve())
            install = item.get("install_command", [])
            if not isinstance(install, list) or not all(isinstance(value, str) and value for value in install):
                raise ValueError("local install commands must be string arrays")
            workers.append(LocalWorker(
                str(item["worker_id"]), tuple(command), str(cwd) if cwd else None, tuple(install)
            ))
        return cls(tuple(workers))

    def start(self, worker_id: str) -> None:
        existing = self._processes.get(worker_id)
        if existing is not None and existing.poll() is None:
            return
        worker = self.workers.get(worker_id)
        if worker is None:
            raise ValueError(f"{worker_id} is not configured as a local worker")
        self._processes[worker_id] = subprocess.Popen(worker.command, cwd=worker.cwd)

    def stop(self, worker_id: str) -> None:
        process = self._processes.pop(worker_id, None)
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def install(self, worker_id: str) -> None:
        worker = self.workers.get(worker_id)
        if worker is None or not worker.install_command:
            raise ValueError(f"{worker_id} has no configured local install command")
        subprocess.Popen(worker.install_command, cwd=worker.cwd)

    def stop_all(self) -> None:
        for worker_id in tuple(self._processes):
            self.stop(worker_id)

    def running(self, worker_id: str) -> bool:
        process = self._processes.get(worker_id)
        return process is not None and process.poll() is None
