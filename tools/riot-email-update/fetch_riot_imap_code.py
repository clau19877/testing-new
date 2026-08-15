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
    ap.add_argument("--timeout", type=float, default=75.0)
    ap.add_argument("--since-seconds", type=float, default=180.0)
    ap.add_argument("--prefix", default="IMAP", help="Env prefix (IMAP or NEW_IMAP)")
    ap.add_argument("--poll", type=float, default=2.0)
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT))
    from imap_mail import ImapConfig, ImapInbox

    cfg = ImapConfig.from_env(dict(os.environ), args.prefix)
    if not cfg:
        print(f"missing {args.prefix}_* settings", file=sys.stderr)
        return 2

    inbox = ImapInbox(cfg)
    since = time.time() - args.since_seconds
    print(
        f"waiting for Riot code on {cfg.user} @{cfg.host} "
        f"(timeout {args.timeout:.0f}s)…",
        file=sys.stderr,
    )
    try:
        code = inbox.wait_for_code(
            since_epoch=since,
            timeout=args.timeout,
            poll_interval=max(0.75, float(args.poll)),
        )
    except TimeoutError:
        print("timeout waiting for code", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"imap error: {exc}", file=sys.stderr)
        return 1
    print(code)  # stdout only — AppleScript reads this
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
