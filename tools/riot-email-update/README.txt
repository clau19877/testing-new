BUILD 2026-08-09j

This zip DOES NOT contain data/tasks.csv. Keep your existing file.

Flow:
  login -> fill emailAddress -> SAVE AND VERIFY
  -> IMAP "Verify Your Email" link -> LOG OUT EVERYWHERE

Update and run (prefer this over Script Editor / old riotemail folder):
  cd ~/Desktop/"riot-email-update-safari-mac 3"
  chmod +x *.sh *.command fetch_riot_imap_code.py fetch_riot_verify_link.py
  ./run_safari_mac.sh

Confirm BUILD 2026-08-09j in debug/logs/safari_*.log
