BUILD 2026-08-09i

This zip DOES NOT contain data/tasks.csv. Keep your existing file.

IMPORTANT:
  Prefer ./run_safari_mac.sh or RUN_ME.command from this folder.
  Do not double-click an older ~/Desktop/riotemail copy of the scripts.

If every account fails with bad_creds:
  1) Confirm BUILD 2026-08-09i in the log
  2) Verify riot_password in data/tasks.csv (wrong password = real reject)
  3) Share debug/logs/safari_*.log

Update and run:
  cd ~/Desktop/"riot-email-update-safari-mac 3"
  chmod +x *.sh *.command fetch_riot_imap_code.py fetch_riot_verify_link.py
  ./run_safari_mac.sh
