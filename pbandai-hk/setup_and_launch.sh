#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ -x "./dist/PBandaiHK" ]]; then
  exec "./dist/PBandaiHK" "$@"
fi

if command -v python3 >/dev/null 2>&1; then
  exec python3 launch.py "$@"
fi

echo "Python 3 was not found. Install python3 and retry."
exit 1
