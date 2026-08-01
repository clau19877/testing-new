#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f config.json ]]; then
  echo "Missing config.json"
  echo "Copy config.example.json to config.json and fill in:"
  echo "  - iCloud IMAP settings"
  echo "  - GrizzlySMS api_key (service bvq = PREMIUM BANDAI)"
  exit 1
fi

if [[ ! -f task.csv ]]; then
  cp task.example.csv task.csv
  echo "Created task.csv from task.example.csv — add your email,password rows, then re-run."
  exit 1
fi

python3 csv_queue.py --dir . init

# Quick IMAP credential sanity check (no mail polling).
python3 - <<'PY'
import json, imaplib, sys
from pathlib import Path
cfg = json.loads(Path("config.json").read_text())
ic = cfg["icloud"]
try:
    c = imaplib.IMAP4_SSL(ic.get("imap_host", "imap.mail.me.com"), int(ic.get("imap_port", 993)))
    c.login(ic["email"], ic["app_specific_password"])
    c.select(ic.get("mailbox", "INBOX"))
    c.logout()
    print("iCloud IMAP: OK")
except Exception as e:
    print(f"iCloud IMAP login failed: {e}", file=sys.stderr)
    print("Create an app-specific password at https://appleid.apple.com", file=sys.stderr)
    sys.exit(1)

g = cfg.get("grizzly") or {}
if g.get("enabled", True):
    if not g.get("api_key") or g.get("api_key") == "YOUR_GRIZZLYSMS_API_KEY":
        print("Set grizzly.api_key in config.json (from https://grizzlysms.com/services)", file=sys.stderr)
        sys.exit(1)
    import subprocess
    out = subprocess.check_output(
        ["/usr/bin/python3", "grizzly_sms.py", "--config", "config.json", "balance"],
        text=True,
    ).strip()
    print(f"GrizzlySMS: {out} (service={g.get('service','bvq')} country={g.get('country',12)})")
PY

pending="$(python3 csv_queue.py --dir . count)"
echo "Tasks pending in task.csv: ${pending}"
if [[ "${pending}" == "0" ]]; then
  echo "task.csv has no rows. Add email,password lines and run again."
  exit 1
fi

echo "Launching Safari signup helper..."
echo "Grant Accessibility permissions if macOS prompts you."
exec osascript "./Signup.applescript"
