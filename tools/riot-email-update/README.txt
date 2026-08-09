BUILD 2026-08-09p

This zip DOES NOT contain data/tasks.csv. Keep your existing file.

Flow: login → change password → change email → IMAP verify → logout

CSV must include new_password (current password stays in riot_password).
Skip password step only: SKIP_PASSWORD_CHANGE=1 ./run_safari_mac.sh

STOP the batch immediately:
  - Ctrl+C in Terminal
  - or double-click STOP_BATCH.command
Remaining CSV rows will NOT run.

Update and run:
  cd ~/Desktop/"riot-email-update-safari-mac 3"
  chmod +x *.sh *.command fetch_riot_imap_code.py fetch_riot_verify_link.py
  ./run_safari_mac.sh

Confirm BUILD 2026-08-09p in debug/logs/safari_*.log
