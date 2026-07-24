#!/usr/bin/env bash
# Batch Safari email updates from a CSV → success.txt / failed.txt
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export TOOL_DIR="$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This batch runner must be run on macOS (Safari)." >&2
  echo "  python3 run_safari_batch.py data/tasks.csv" >&2
  exit 1
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

CSV="${1:-data/tasks.csv}"
shift || true
exec python3 "$ROOT/run_safari_batch.py" "$CSV" "$@"
