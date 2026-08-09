#!/usr/bin/env python3
"""Wait for Riot's "Verify Your Email" message and print its verification URL."""

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
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--since-seconds", type=float, default=300.0)
    parser.add_argument(
        "--since-epoch",
        type=float,
        help="Only accept mail received after this Unix timestamp",
    )
    parser.add_argument("--prefix", default="IMAP")
    parser.add_argument("--subject", default="Verify Your Email")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from imap_mail import ImapConfig, ImapInbox

    config = ImapConfig.from_env(dict(os.environ), args.prefix)
    if not config:
        print(f"missing {args.prefix}_* settings", file=sys.stderr)
        return 2

    inbox = ImapInbox(config)
    since = (
        args.since_epoch - 5
        if args.since_epoch is not None
        else time.time() - args.since_seconds
    )
    deadline = time.time() + args.timeout
    wanted_subject = args.subject.casefold().strip()
    print(
        f'waiting for Riot "{args.subject}" link on {config.user} @{config.host}…',
        file=sys.stderr,
    )

    while time.time() < deadline:
        try:
            messages = inbox.fetch_recent_riot_mail(since_epoch=since)
        except Exception as exc:
            print(f"imap poll: {exc}", file=sys.stderr)
            messages = []

        for message in messages:
            if wanted_subject not in message.subject.casefold():
                continue
            if message.verify_link:
                print(f'found verify link in "{message.subject}"', file=sys.stderr)
                print(message.verify_link)
                return 0
        time.sleep(4)

    print(f'timeout waiting for "{args.subject}" verification link', file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
