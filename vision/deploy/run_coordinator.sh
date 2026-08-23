#!/usr/bin/env bash
set -euo pipefail
VISION_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${1:-$VISION_ROOT/coordinator/config.toml}"
exec "$VISION_ROOT/.venv-coordinator/bin/desk-buddy-vision-coordinator" --config "$CONFIG_PATH"

