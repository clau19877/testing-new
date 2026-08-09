#!/bin/bash
# Double-click to hard-stop the Safari batch immediately (no more accounts).
cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
FLAG="$ROOT/.safari_batch_stop"
PIDFILE="$ROOT/.safari_batch.pid"

echo "Stopping Safari Riot batch…"
echo "  folder: $ROOT"
date +%s > "$FLAG" 2>/dev/null || echo stop > "$FLAG"

# Kill the batch python (and its process group) if we know the PID.
if [[ -f "$PIDFILE" ]]; then
  PID="$(tr -d '[:space:]' < "$PIDFILE" 2>/dev/null || true)"
  if [[ -n "$PID" ]]; then
    echo "  batch pid: $PID"
    kill -TERM "-$PID" 2>/dev/null || true
    kill -TERM "$PID" 2>/dev/null || true
    sleep 0.4
    kill -KILL "-$PID" 2>/dev/null || true
    kill -KILL "$PID" 2>/dev/null || true
  fi
fi

# Also kill leftover osascript runners for this toolkit.
pkill -TERM -f "safari_riot_email.applescript" 2>/dev/null || true
pkill -TERM -f "run_safari_batch.py" 2>/dev/null || true
sleep 0.3
pkill -KILL -f "safari_riot_email.applescript" 2>/dev/null || true
pkill -KILL -f "run_safari_batch.py" 2>/dev/null || true

echo
echo "Stop signal sent. Remaining tasks will not start."
echo "You can close this window."
sleep 2
