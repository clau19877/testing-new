BUILD 2026-08-12c

This zip DOES NOT contain data/tasks.csv. Keep your existing file.

Script Editor fix: launch uses launch_safari_batch.sh (no \" in .applescript).

Prefer:
  ./run_safari_mac.sh
  or double-click RUN_ME.command

Flow: email first, then optional password (CSV change_password 1/0).

Background Safari (default): does not steal focus.
  SAFARI_STEAL_FOCUS=1 ./run_safari_mac.sh   # only if you need frontmost

Confirm BUILD 2026-08-12c in debug/logs/safari_*.log
