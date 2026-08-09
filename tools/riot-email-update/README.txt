BUILD 2026-08-09t

This zip DOES NOT contain data/tasks.csv. Keep your existing file.

On SUCCESS:
  - written to success.txt
  - that row is removed from data/tasks.csv

On FAILURE:
  - failed.txt = CSV rows (same header as tasks.csv) — paste back to rerun
  - failed_reasons.txt = username, reason, log path
  - riot_password in failed.txt is set to new_password when present

STOP fully:
  - Ctrl+C or double-click STOP_BATCH.command

Update and run:
  cd ~/Desktop/"riot-email-update-safari-mac 3"
  chmod +x *.sh *.command fetch_riot_imap_code.py fetch_riot_verify_link.py stop_safari_batch.sh
  ./run_safari_mac.sh

Confirm BUILD 2026-08-09t in debug/logs/safari_*.log
