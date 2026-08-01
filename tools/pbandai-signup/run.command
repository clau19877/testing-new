#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f config.json ]]; then
  echo "Missing config.json"
  echo "Copy config.example.json to config.json and fill in:"
  echo "  - iCloud email + app-specific password"
  echo "  - Premium Bandai profile fields"
  exit 1
fi

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
    print("Create an app-specific password at https://appleid.apple.com (Sign-In and Security → App-Specific Passwords).", file=sys.stderr)
    sys.exit(1)
PY

echo "Launching Safari signup helper..."
echo "Grant Accessibility permissions if macOS prompts you."
exec osascript "./Signup.applescript"
