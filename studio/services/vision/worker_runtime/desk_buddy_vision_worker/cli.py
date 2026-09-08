"""Command-line boundary for installation, service launch, and build checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .catalog import validate_model


def _event(phase: str, message: str, **values) -> None:
    print(json.dumps({"event": "progress", "phase": phase, "message": message, **values}), flush=True)


def _config(path: str) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("model"), dict):
        raise ValueError("invalid vision worker configuration")
    validate_model(value["model"])
    return value


def install(path: str) -> int:
    config = _config(path)
    from huggingface_hub import snapshot_download

    model = config["model"]
    cache = Path(model["cache_dir"])
    cache.mkdir(parents=True, exist_ok=True)
    _event("model", f"Downloading {model['source']} at {str(model['revision'])[:12]}…")
    snapshot_download(
        repo_id=str(model["source"]),
        revision=str(model["revision"]),
        cache_dir=str(cache),
        local_files_only=False,
    )
    _event("model", "Pinned model snapshot downloaded")
    return 0


def serve(path: str) -> int:
    from .service import DetectorService

    return DetectorService(_config(path)).run()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Desk Buddy zero-shot vision worker")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "serve"):
        command = sub.add_parser(name)
        command.add_argument("--config", required=True)
    sub.add_parser("self-test")
    args = parser.parse_args(argv)
    try:
        if args.command == "self-test":
            from .frames import decode_frame, encode_frame
            payload = encode_frame({"action_id": "self-test"}, b"\xff\xd8ok\xff\xd9")
            assert decode_frame(payload).metadata["action_id"] == "self-test"
            print(json.dumps({"ok": True, "worker": "desk-buddy-vision"}))
            return 0
        if args.command == "install":
            return install(args.config)
        return serve(args.config)
    except Exception as exc:
        message = " ".join(str(exc).split())[:500] or type(exc).__name__
        _event("error", message)
        return 1
