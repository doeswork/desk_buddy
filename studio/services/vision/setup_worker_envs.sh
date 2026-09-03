#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 WORKER_ENV_DIRECTORY" >&2
  exit 2
fi

env_root=$1
python_bin=${PYTHON_BIN:-python3}
mkdir -p "$env_root"

for worker_name in zero-shot zero-shot-ensemble depth training inference; do
  "$python_bin" -m venv "$env_root/$worker_name"
done

"$env_root/zero-shot/bin/python" -m pip install -r studio/services/vision/zero_shot/requirements.txt
"$env_root/zero-shot-ensemble/bin/python" -m pip install -r studio/services/vision/zero_shot/requirements.txt
"$env_root/depth/bin/python" -m pip install -r studio/services/vision/depth/requirements.txt
"$env_root/training/bin/python" -m pip install -r studio/services/vision/model_builder/requirements.txt
"$env_root/inference/bin/python" -m pip install -r studio/services/vision/model_builder/requirements.txt

echo "Created isolated worker environments under $env_root"
