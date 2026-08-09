BUILD 2026-08-09r

This zip DOES NOT contain data/tasks.csv. Keep your existing file.

Flow: login → change password → re-login if session dropped →
      change email → IMAP verify (recipient-aware) → logout

CSV must include new_password (current password stays in riot_password).
Skip password step only: SKIP_PASSWORD_CHANGE=1 ./run_safari_mac.sh

Re-run failed rows carefully:
  - If password already changed, put that value in riot_password
  - Or SKIP_PASSWORD_CHANGE=1 with riot_password already updated

STOP the batch immediately:
  - Ctrl+C in Terminal
  - or double-click STOP_BATCH.command

Update and run:
  cd ~/Desktop/"riot-email-update-safari-mac 3"
  chmod +x *.sh *.command fetch_riot_imap_code.py fetch_riot_verify_link.py
  ./run_safari_mac.sh

Confirm BUILD 2026-08-09r in debug/logs/safari_*.log
