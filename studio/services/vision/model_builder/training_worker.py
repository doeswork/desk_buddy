"""Command-line entry point for the isolated MLP training process."""

from __future__ import annotations

import argparse

from ..config import load_worker_settings
from ..run_worker import run_worker
from .training import IKTrainingHandler


def main() -> int:
    parser = argparse.ArgumentParser(description="Desk Buddy IK training MQTT worker")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    settings = load_worker_settings(args.config)
    handler = IKTrainingHandler(device=settings.device)
    return run_worker(settings, {handler.model_id: handler})


if __name__ == "__main__":
    raise SystemExit(main())
