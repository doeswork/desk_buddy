#!/usr/bin/env bash
set -euo pipefail
VISION_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${1:-$VISION_ROOT/services/detector_huggingface/config.toml}"
exec "$VISION_ROOT/.venv-detector-huggingface/bin/desk-buddy-detector-hf" --config "$CONFIG_PATH"

