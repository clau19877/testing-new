#!/usr/bin/env bash
#
# One-click launcher for the Riot email-update batch runner (manual captcha).
#
# What it does:
#   1. Sets up the Python venv + browser on first run
#   2. Loads .env (proxies, optional settings)
#   3. Ensures a viewable browser:
#        - macOS / a real desktop  → uses your normal screen
#        - headless Linux / cloud VM → starts Xvfb + VNC + noVNC (:6080)
#   4. Runs every row in data/tasks.csv, pausing for you to solve each captcha
#
# Usage:
#   ./launch.sh                 # runs data/tasks.csv
#   ./launch.sh my-tasks.csv    # runs a specific CSV
#   ./launch.sh --dry-run       # just validate the CSV
#
set -euo pipefail
cd "$(dirname "$0")"

CSV_ARG=""
EXTRA_ARGS=()
for a in "$@"; do
  case "$a" in
    --*) EXTRA_ARGS+=("$a") ;;
    *)   CSV_ARG="$a" ;;
  esac
done
TASKS_CSV="${CSV_ARG:-data/tasks.csv}"

echo "== Riot email-update launcher =="

# 1) venv + deps
if [ ! -x ".venv/bin/python" ]; then
  echo "[setup] creating virtualenv…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  echo "[setup] installing requirements (this runs once)…"
  ./.venv/bin/pip install --quiet -r requirements.txt
  echo "[setup] installing browser…"
  ./.venv/bin/python -m patchright install chromium >/dev/null 2>&1 || \
    ./.venv/bin/python -m playwright install chromium >/dev/null 2>&1 || true
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# 2) .env
if [ -f ".env" ]; then
  set -a; # shellcheck disable=SC1091
  source .env; set +a
fi

# 3) task file check
if [ ! -f "$TASKS_CSV" ]; then
  if [ -f "data/tasks.csv.example" ] && [ "$TASKS_CSV" = "data/tasks.csv" ]; then
    mkdir -p data
    cp data/tasks.csv.example data/tasks.csv
    echo ""
    echo "  Created data/tasks.csv from the template."
    echo "  → Open it, fill in your account rows (login, password, imap email,"
    echo "    app password, new email), then run ./launch.sh again."
    exit 0
  fi
  echo "Task file not found: $TASKS_CSV" >&2
  exit 1
fi

# 4) display: use a real one if present, else start VNC on the VM
NEED_VNC=0
if [ "$(uname -s)" = "Darwin" ]; then
  : # macOS: Playwright opens a real window; nothing to do
elif [ -n "${DISPLAY:-}" ] && command -v xdpyinfo >/dev/null 2>&1 && xdpyinfo >/dev/null 2>&1; then
  echo "[display] using existing DISPLAY=$DISPLAY"
else
  NEED_VNC=1
fi

if [ "$NEED_VNC" = "1" ]; then
  echo "[display] no usable display — starting Xvfb + VNC + noVNC…"
  bash ./start_manual_session.sh || true
  export DISPLAY=":${DISPLAY_NUM:-99}"
  echo "[display] open http://<this-host>:${NOVNC_PORT:-6080}/vnc.html to watch/solve"
fi

# 5) run the batch
echo "[run] starting tasks from $TASKS_CSV"
exec python run_tasks.py "$TASKS_CSV" "${EXTRA_ARGS[@]}"
