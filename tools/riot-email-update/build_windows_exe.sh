#!/usr/bin/env bash
# Cross-compile RiotEmailUpdate.exe (Windows amd64) from Linux/macOS/Windows+Go.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p windows
cd windows
echo "[build] GOOS=windows GOARCH=amd64 → ../RiotEmailUpdate.exe"
GOOS=windows GOARCH=amd64 go build -trimpath -ldflags="-s -w" -o ../RiotEmailUpdate.exe .
ls -lh ../RiotEmailUpdate.exe
echo "[build] done"
