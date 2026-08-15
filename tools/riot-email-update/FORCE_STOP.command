#!/bin/bash
# ============================================================
#  FORCE STOP — double-click this to kill the Safari batch NOW
# ============================================================
# Same as STOP_BATCH.command. Stops all accounts immediately.
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
