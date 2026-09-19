#!/usr/bin/env bash
# Run Studio's test modules with the interpreter that actually has Qt.
#
# The tests are plain modules, not a pytest suite, and every one of them
# imports PySide6 somewhere. On a distro with an externally-managed Python
# (Arch, Debian, Fedora) that lives in the repo's .venv and nowhere else, so
# a documented `python -m studio...` copied into a shell fails on import
# before it runs a single assertion — which looks exactly like a broken test
# rather than the wrong interpreter. This picks .venv and sets the offscreen
# platform, so the tests just work.
#
#   studio/run_tests.sh                  # everything
#   studio/run_tests.sh calibration      # modules matching a substring
#
# Exits non-zero if any module fails.

set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

python="$root/.venv/bin/python"
if [[ ! -x "$python" ]]; then
  echo "No .venv found. Create it first (see studio/README.md):" >&2
  echo "  python3 -m venv .venv" >&2
  echo "  .venv/bin/pip install -r studio/requirements.txt" >&2
  exit 1
fi

if ! "$python" -c 'import PySide6' 2>/dev/null; then
  echo "$python has no PySide6. Install the requirements:" >&2
  echo "  .venv/bin/pip install -r studio/requirements.txt" >&2
  exit 1
fi

export QT_QPA_PLATFORM=offscreen

modules=$(
  find studio \( -name 'tests.py' -o -name '*_tests.py' -o -name 'smoke_test.py' \) \
    -not -path '*/__pycache__/*' |
  sed 's|/|.|g; s|\.py$||' | sort
)

if [[ $# -gt 0 ]]; then
  modules=$(printf '%s\n' "$modules" | grep -- "$1") || {
    echo "No test modules match '$1'." >&2
    exit 1
  }
fi

failed=()
for module in $modules; do
  if output=$("$python" -m "$module" 2>&1); then
    printf 'PASS  %-52s %s\n' "$module" "$(printf '%s' "$output" | tail -1)"
  else
    printf 'FAIL  %-52s\n' "$module"
    printf '%s\n' "$output" | tail -15 | sed 's/^/        /'
    failed+=("$module")
  fi
done

echo
if [[ ${#failed[@]} -eq 0 ]]; then
  echo "All test modules passed."
else
  echo "${#failed[@]} module(s) failed:"
  printf '  %s\n' "${failed[@]}"
  exit 1
fi
