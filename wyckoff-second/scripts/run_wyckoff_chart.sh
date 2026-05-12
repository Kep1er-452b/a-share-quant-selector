#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

candidates=()
if [[ -n "${CODEX_WORKSPACE_PYTHON:-}" ]]; then
  candidates+=("$CODEX_WORKSPACE_PYTHON")
fi
candidates+=(
  "/Users/chenxingyu/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
  "python3"
)

for py in "${candidates[@]}"; do
  if ! command -v "$py" >/dev/null 2>&1; then
    continue
  fi
  if "$py" - <<'PY' >/dev/null 2>&1
import pandas
import numpy
from PIL import Image
PY
  then
    exec "$py" "$SCRIPT_DIR/wyckoff_chart.py" "$@"
  fi
done

echo "No usable Python runtime found. Need pandas, numpy, and Pillow." >&2
exit 1
