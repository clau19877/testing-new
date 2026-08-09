#!/bin/bash
# Double-click this file on your Mac (or run it in Terminal).
# Works even when the folder is named "riot-email-update-safari-mac 3".

cd "$(dirname "$0")" || exit 1
chmod +x run_safari_mac.sh run_safari_batch.sh fetch_riot_imap_code.py RUN_ME.command 2>/dev/null || true

echo "BUILD 2026-08-09d"
echo "Working folder: $(pwd)"
echo "Looking for data/tasks.csv ..."
if [[ ! -f data/tasks.csv && ! -f tasks.csv ]]; then
  echo
  echo "ERROR: Put your account list here:"
  echo "  $(pwd)/data/tasks.csv"
  echo
  echo "Press Enter to close."
  read -r
  exit 2
fi

./run_safari_mac.sh
echo
echo "Done. Press Enter to close."
read -r
