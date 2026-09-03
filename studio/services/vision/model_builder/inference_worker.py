"""Command-line entry point for an isolated MLP inference process."""

from __future__ import annotations

import argparse

from ..config import load_worker_settings
from ..run_worker import run_worker
from .inference import IKInferenceHandler


def main() -> int:
    parser = argparse.ArgumentParser(description="Desk Buddy IK inference MQTT worker")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    settings = load_worker_settings(args.config)
    handler = IKInferenceHandler(device=settings.device)
    return run_worker(settings, {"*": handler})


if __name__ == "__main__":
    raise SystemExit(main())
