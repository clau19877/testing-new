#!/bin/bash
# Launch run_safari_batch.py in the background, record PID, wait until exit or stop flag.
# Called from AppleScript (keeps complex shell out of .applescript source).
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1

CSV="${1:-}"
if [[ -z "$CSV" || ! -f "$CSV" ]]; then
  echo "usage: $0 /path/to/tasks.csv" >&2
  exit 2
fi

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="$(command -v python3)"
fi

chmod +x \
  "$ROOT/stop_safari_batch.sh" \
  "$ROOT/STOP_BATCH.command" \
  "$ROOT/run_safari_mac.sh" \
  "$ROOT/run_safari_batch.py" \
  2>/dev/null || true

rm -f "$ROOT/.safari_batch_stop"
export TOOL_DIR="$ROOT"

LOG="$ROOT/.safari_batch_console.log"
PIDFILE="$ROOT/.safari_batch.pid"

"$PY" "$ROOT/run_safari_batch.py" "$CSV" >"$LOG" 2>&1 &
echo $! >"$PIDFILE"
pid="$(cat "$PIDFILE")"

while kill -0 "$pid" 2>/dev/null; do
  if [[ -f "$ROOT/.safari_batch_stop" ]]; then
    /bin/bash "$ROOT/stop_safari_batch.sh" >/dev/null 2>&1 || true
    break
  fi
  sleep 0.5
done

wait "$pid" 2>/dev/null || true
exit 0
