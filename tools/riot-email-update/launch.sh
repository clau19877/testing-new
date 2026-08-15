#!/usr/bin/env bash
#
# One-click launcher for the Riot email-update batch runner (manual captcha).
# Thin wrapper around launch.py (same logic as RiotEmailUpdate.exe / launch.bat).
#
# Usage:
#   ./launch.sh                 # runs data/tasks.csv
#   ./launch.sh my-tasks.csv    # runs a specific CSV
#   ./launch.sh --dry-run       # just validate the CSV
#
set -euo pipefail
cd "$(dirname "$0")"
if [ -x ".venv/bin/python" ]; then
  exec .venv/bin/python launch.py "$@"
fi
exec python3 launch.py "$@"
