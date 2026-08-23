#!/usr/bin/env bash
set -euo pipefail
VISION_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${1:-$VISION_ROOT/services/planner_training/config.toml}"
exec "$VISION_ROOT/.venv-planner-training/bin/desk-buddy-planner-training" --config "$CONFIG_PATH"

