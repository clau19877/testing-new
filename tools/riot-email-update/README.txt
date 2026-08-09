BUILD 2026-08-09m

This zip DOES NOT contain data/tasks.csv. Keep your existing file.

Cloudflare / warm-up:
  - Script opens apple.com first, then docs.qq → Riot
  - Jittered delays; waits for real *.riotgames.com hostname
  - Detects "Just a moment" / CF and backs off
  - Default gap between accounts ~25s + jitter
  If CF still hits: SAFARI_BATCH_DELAY=40 ./run_safari_mac.sh

Update and run:
  cd ~/Desktop/"riot-email-update-safari-mac 3"
  chmod +x *.sh *.command fetch_riot_imap_code.py fetch_riot_verify_link.py
  ./run_safari_mac.sh

Confirm BUILD 2026-08-09m in debug/logs/safari_*.log
