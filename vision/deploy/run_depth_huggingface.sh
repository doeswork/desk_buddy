#!/usr/bin/env bash
set -euo pipefail
VISION_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${1:-$VISION_ROOT/services/depth_huggingface/config.toml}"
exec "$VISION_ROOT/.venv-depth-huggingface/bin/desk-buddy-depth-hf" --config "$CONFIG_PATH"

