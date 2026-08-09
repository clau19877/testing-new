#!/bin/bash
# Hard-stop the Safari Riot batch and every helper process.
# Used by STOP_BATCH.command and by the AppleScript cancel path.
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1

FLAG="$ROOT/.safari_batch_stop"
PIDFILE="$ROOT/.safari_batch.pid"

echo "⏹  Hard-stopping Safari Riot batch…"
echo "  folder: $ROOT"

# 1) Signal the Python loop (checked between accounts / while waiting on osascript).
printf 'stop %s\n' "$(date +%s)" >"$FLAG" 2>/dev/null || echo stop >"$FLAG"

kill_pid_tree() {
  local pid="${1:-}"
  [[ -n "$pid" && "$pid" =~ ^[0-9]+$ ]] || return 0
  # Children first (osascript / IMAP helpers), then the parent.
  local kids
  kids="$(pgrep -P "$pid" 2>/dev/null || true)"
  if [[ -n "$kids" ]]; then
    local c
    for c in $kids; do
      kill_pid_tree "$c"
    done
  fi
  kill -TERM "$pid" 2>/dev/null || true
  kill -KILL "$pid" 2>/dev/null || true
}

# 2) Kill the recorded batch PID and its whole tree / process group.
if [[ -f "$PIDFILE" ]]; then
  PID="$(tr -d '[:space:]' <"$PIDFILE" 2>/dev/null || true)"
  if [[ -n "${PID:-}" ]]; then
    echo "  batch pid: $PID"
    # Process-group kill (works when batch started in its own session).
    kill -TERM "-$PID" 2>/dev/null || true
    kill_pid_tree "$PID"
    sleep 0.25
    kill -KILL "-$PID" 2>/dev/null || true
    kill -KILL "$PID" 2>/dev/null || true
  fi
fi

# 3) Sweep leftovers by command line (covers Terminal / Script Editor / IMAP waits).
patterns=(
  "run_safari_batch.py"
  "safari_riot_email.applescript"
  "fetch_riot_verify_link.py"
  "fetch_riot_imap_code.py"
  "run_safari_mac.sh"
)
for pat in "${patterns[@]}"; do
  pkill -TERM -f "$pat" 2>/dev/null || true
done
sleep 0.35
for pat in "${patterns[@]}"; do
  pkill -KILL -f "$pat" 2>/dev/null || true
done

# 4) Any osascript still attached to this toolkit folder.
pkill -TERM -f "$ROOT/safari_riot_email.applescript" 2>/dev/null || true
pkill -KILL -f "$ROOT/safari_riot_email.applescript" 2>/dev/null || true

# 5) Stop in-flight Safari navigation (best-effort; ignore JS permission errors).
osascript <<'APPLESCRIPT' >/dev/null 2>&1 || true
tell application "Safari"
  try
    if (count of documents) > 0 then
      do JavaScript "try { window.stop(); } catch (e) {}" in document 1
    end if
  end try
end tell
APPLESCRIPT

# 6) Clear PID file so a later run does not kill the wrong process.
rm -f "$PIDFILE" 2>/dev/null || true

still="$(pgrep -fl 'run_safari_batch.py|safari_riot_email.applescript|fetch_riot_verify_link.py' 2>/dev/null || true)"
if [[ -n "$still" ]]; then
  echo "  warning: some processes may still be visible:"
  echo "$still" | sed 's/^/    /'
  echo "  retry STOP_BATCH.command, or quit Terminal / Script Editor."
else
  echo "  all batch / osascript / IMAP helper processes cleared."
fi

echo
echo "Stop complete. Remaining CSV rows will not run."
echo "You can close this window."
