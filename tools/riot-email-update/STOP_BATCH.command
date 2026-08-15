#!/bin/bash
# Double-click to FORCE STOP the Safari batch immediately (no more accounts).
# Alias: FORCE_STOP.command (same action).
cd "$(dirname "$0")" || exit 1
chmod +x stop_safari_batch.sh STOP_BATCH.command FORCE_STOP.command 2>/dev/null || true

echo
echo "############################################"
echo "#  FORCE STOP — killing Safari batch now  #"
echo "############################################"
echo

/bin/bash "./stop_safari_batch.sh"

echo
echo "Done. Batch should be stopped."
echo "Press Enter to close."
read -r || true
