"""Explicitly inspect and download a configured Hugging Face provider model."""

from __future__ import annotations

import argparse
import json

from .config import load_worker_settings
from .depth.huggingface import HuggingFaceDepthHandler
from .zero_shot.huggingface import HuggingFaceZeroShotHandler


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or install one configured vision model")
    parser.add_argument("--config", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--accept-license", action="store_true")
    parser.add_argument("--allow-moving-revision", action="store_true")
    args = parser.parse_args()
    settings = load_worker_settings(args.config)
    model = settings.models.get(args.model_id)
    if model is None:
        raise SystemExit(f"model {args.model_id!r} is not in {args.config}")
    details = {
        "model_id": args.model_id,
        "kind": settings.worker_kind,
        "source": model.get("source"),
        "revision": model.get("revision"),
        "license": model.get("license", "unknown — verify before installation"),
        "size_mb": model.get("size_mb", "unknown"),
        "devices": model.get("devices", ["cpu"]),
    }
    print(json.dumps(details, indent=2))
    if not args.install:
        print("Inspection only. Re-run with --install --accept-license to download into this worker environment.")
        return 0
    if not args.accept_license:
        raise SystemExit("installation requires --accept-license after reviewing the metadata above")
    revision = str(model.get("revision") or "")
    if revision.lower() in {"", "main", "master", "latest"} and not args.allow_moving_revision:
        raise SystemExit("refusing a moving revision; configure an immutable model commit")
    if settings.worker_kind in {"zero_shot", "zero-shot"}:
        handler = HuggingFaceZeroShotHandler(
            model_id=args.model_id, source=str(model["source"]), revision=revision,
            local_files_only=False, device=settings.device,
            prompt_style=str(model.get("prompt_style") or "auto"),
        )
    elif settings.worker_kind == "depth":
        handler = HuggingFaceDepthHandler(
            model_id=args.model_id, source=str(model["source"]), revision=revision,
            local_files_only=False, device=settings.device,
            native_near_is_high=bool(model.get("native_near_is_high", True)),
        )
    else:
        raise SystemExit("only zero-shot and depth model installations are supported")
    handler.load()
    print(f"Installed {args.model_id} at revision {revision} in this worker's model cache.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
