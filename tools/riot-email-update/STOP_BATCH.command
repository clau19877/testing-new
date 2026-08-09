#!/bin/bash
# Double-click to hard-stop the Safari batch immediately (no more accounts).
cd "$(dirname "$0")" || exit 1
chmod +x stop_safari_batch.sh 2>/dev/null || true
/bin/bash "./stop_safari_batch.sh"
echo
echo "Press Enter to close."
read -r || true
