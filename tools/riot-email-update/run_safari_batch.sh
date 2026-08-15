#!/usr/bin/env bash
# Batch Safari email updates from a CSV → success.txt / failed.txt
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export TOOL_DIR="$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This batch runner must be run on macOS (Safari)." >&2
  exit 1
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

CSV="${1:-}"
if [[ -z "$CSV" ]]; then
  if [[ -f "$ROOT/data/tasks.csv" ]]; then
    CSV="$ROOT/data/tasks.csv"
  elif [[ -f "$ROOT/tasks.csv" ]]; then
    CSV="$ROOT/tasks.csv"
  else
    echo "CSV not found. Put accounts in: $ROOT/data/tasks.csv" >&2
    exit 2
  fi
  shift || true
else
  shift || true
  if [[ ! -f "$CSV" && -f "$ROOT/$CSV" ]]; then
    CSV="$ROOT/$CSV"
  fi
fi

echo "script folder: $ROOT"
echo "csv: $CSV"
exec python3 "$ROOT/run_safari_batch.py" "$CSV" "$@"
