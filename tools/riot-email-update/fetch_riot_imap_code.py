#!/usr/bin/env python3
"""Fetch the latest Riot verification code from IMAP (for Safari AppleScript).

Usage:
  python fetch_riot_imap_code.py [--timeout 120] [--since-seconds 180]

Reads IMAP_* / NEW_IMAP_* from environment / .env in this folder.
Prints only the code to stdout on success (status on stderr).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except Exception:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    _load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--since-seconds", type=float, default=180.0)
    ap.add_argument("--prefix", default="IMAP", help="Env prefix (IMAP or NEW_IMAP)")
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT))
    from imap_mail import ImapConfig, ImapInbox

    cfg = ImapConfig.from_env(dict(os.environ), args.prefix)
    if not cfg:
        print(f"missing {args.prefix}_* settings", file=sys.stderr)
        return 2

    inbox = ImapInbox(cfg)
    since = time.time() - args.since_seconds
    deadline = time.time() + args.timeout
    used: set[str] = set()
    print(f"waiting for Riot code on {cfg.user} @{cfg.host}…", file=sys.stderr)
    while time.time() < deadline:
        try:
            mails = inbox.fetch_recent_riot_mail(since_epoch=since)
        except Exception as exc:
            print(f"imap poll: {exc}", file=sys.stderr)
            mails = []
        for mail in mails:
            if mail.code and mail.code not in used:
                print(f"found code in “{mail.subject}”", file=sys.stderr)
                print(mail.code)  # stdout only — AppleScript reads this
                return 0
        time.sleep(4)
    print("timeout waiting for code", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
