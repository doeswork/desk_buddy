"""Run an isolated zero-shot worker: python -m ...zero_shot.worker_app CONFIG."""

from __future__ import annotations

import argparse

from ..config import load_worker_settings
from ..run_worker import run_worker
from .huggingface import HuggingFaceZeroShotHandler


def main() -> int:
    parser = argparse.ArgumentParser(description="Desk Buddy zero-shot MQTT worker")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    settings = load_worker_settings(args.config)
    handlers = {}
    for model_id, model in settings.models.items():
        handler = HuggingFaceZeroShotHandler(
            model_id=model_id,
            source=str(model["source"]),
            revision=str(model["revision"]),
            local_files_only=bool(model.get("local_files_only", True)),
            device=settings.device,
            prompt_style=str(model.get("prompt_style") or "auto"),
        )
        handler.load()
        handlers[model_id] = handler
    return run_worker(settings, handlers)


if __name__ == "__main__":
    raise SystemExit(main())
