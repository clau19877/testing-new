BUILD 2026-08-09k

This zip DOES NOT contain data/tasks.csv. Keep your existing file.

IMAP fix: iCloud rewrites Riot's From address and rejects RFC822 fetches.
The toolkit now uses BODY.PEEK[] and recognizes mangled Riot senders, so
"Verify Your Email" links are found.

Update and run:
  cd ~/Desktop/"riot-email-update-safari-mac 3"
  chmod +x *.sh *.command fetch_riot_imap_code.py fetch_riot_verify_link.py
  ./run_safari_mac.sh

Confirm BUILD 2026-08-09k in debug/logs/safari_*.log
