"""Run an isolated depth worker: python -m ...depth.worker_app CONFIG."""

from __future__ import annotations

import argparse

from ..config import load_worker_settings
from ..run_worker import run_worker
from .huggingface import HuggingFaceDepthHandler


def main() -> int:
    parser = argparse.ArgumentParser(description="Desk Buddy depth MQTT worker")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    settings = load_worker_settings(args.config)
    handlers = {}
    for model_id, model in settings.models.items():
        handler = HuggingFaceDepthHandler(
            model_id=model_id,
            source=str(model["source"]),
            revision=str(model["revision"]),
            local_files_only=bool(model.get("local_files_only", True)),
            device=settings.device,
            native_near_is_high=bool(model.get("native_near_is_high", True)),
        )
        handler.load()
        handlers[model_id] = handler
    return run_worker(settings, handlers)


if __name__ == "__main__":
    raise SystemExit(main())
