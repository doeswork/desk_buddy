#!/usr/bin/env bash
set -euo pipefail

VISION_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
if [[ "$#" -eq 0 ]]; then
  REQUESTED_SERVICES=(all)
else
  REQUESTED_SERVICES=("$@")
fi

wants_service() {
  local candidate="$1"
  local requested
  for requested in "${REQUESTED_SERVICES[@]}"; do
    if [[ "$requested" == "all" || "$requested" == "$candidate" ]]; then
      return 0
    fi
  done
  return 1
}

create_environment() {
  local environment_path="$1"
  local project_path="$2"
  "$PYTHON_BIN" -m venv "$environment_path"
  "$environment_path/bin/python" -m pip install --upgrade pip
  "$environment_path/bin/python" -m pip install "$VISION_ROOT/protocol"
  "$environment_path/bin/python" -m pip install "$project_path"
}

if wants_service coordinator; then
  create_environment "$VISION_ROOT/.venv-coordinator" "$VISION_ROOT/coordinator"
fi
if wants_service detector; then
  create_environment "$VISION_ROOT/.venv-detector-huggingface" "$VISION_ROOT/services/detector_huggingface"
fi
if wants_service depth; then
  create_environment "$VISION_ROOT/.venv-depth-huggingface" "$VISION_ROOT/services/depth_huggingface"
fi
if wants_service planner; then
  create_environment "$VISION_ROOT/.venv-planner-training" "$VISION_ROOT/services/planner_training"
fi
