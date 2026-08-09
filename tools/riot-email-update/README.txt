BUILD 2026-08-09s

This zip DOES NOT contain data/tasks.csv. Keep your existing file.

STOP the batch immediately (fully):
  - Ctrl+C in Terminal
  - or double-click STOP_BATCH.command
This kills python + osascript + IMAP helpers (not just a soft flag).

Flow: login → change password → re-login if session dropped →
      change email → IMAP verify → logout

Update and run:
  cd ~/Desktop/"riot-email-update-safari-mac 3"
  chmod +x *.sh *.command fetch_riot_imap_code.py fetch_riot_verify_link.py stop_safari_batch.sh
  ./run_safari_mac.sh

Confirm BUILD 2026-08-09s in debug/logs/safari_*.log
