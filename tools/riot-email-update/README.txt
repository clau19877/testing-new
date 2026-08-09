BUILD 2026-08-09h

This zip DOES NOT contain data/tasks.csv. Keep your existing file.

Exact flow per account:
  login -> account.riotgames.com
  -> fill personal-information-card__emailAddress
  -> click personal-information-card__saveChanges-btn
  -> IMAP: find subject "Verify Your Email" and open Verify Email link
  -> click log-out-everywhere-button
  -> next CSV account

Investigation logs (share these when something fails):
  debug/logs/safari_YYYYMMDD_HHMMSS_<username>.log

Update and run:
  cd ~/Desktop/"riot-email-update-safari-mac 3"
  chmod +x *.sh *.command fetch_riot_imap_code.py fetch_riot_verify_link.py
  ./run_safari_mac.sh
