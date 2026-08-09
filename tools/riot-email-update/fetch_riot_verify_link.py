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


def _pick_link(messages, *, wanted_subject: str, recipient: str):
    """Prefer newest verify mail that mentions recipient; else newest verify mail."""
    recip = (recipient or "").casefold().strip()
    matched = []
    fallback = []
    for message in messages:
        if wanted_subject not in message.subject.casefold():
            # Still accept close subjects that carry a verify link.
            if "verify" not in message.subject.casefold():
                continue
        if not message.verify_link:
            continue
        if recip and recip in (message.recipients or ""):
            matched.append(message)
        else:
            fallback.append(message)
    if matched:
        return matched[0]
    if recip:
        return None
    if fallback:
        return fallback[0]
    return None


def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--since-seconds", type=float, default=300.0)
    parser.add_argument(
        "--since-epoch",
        type=float,
        help="Only accept mail received after this Unix timestamp",
    )
    parser.add_argument("--prefix", default="IMAP")
    parser.add_argument("--subject", default="Verify Your Email")
    parser.add_argument(
        "--recipient",
        default="",
        help="Prefer verify mail addressed to / mentioning this email (new_email)",
    )
    args = parser.parse_args()
    if not args.recipient:
        args.recipient = (
            os.getenv("NEW_EMAIL") or os.getenv("VERIFY_RECIPIENT") or ""
        ).strip()

    sys.path.insert(0, str(ROOT))
    from imap_mail import ImapConfig, ImapInbox

    config = ImapConfig.from_env(dict(os.environ), args.prefix)
    if not config:
        print(f"missing {args.prefix}_* settings", file=sys.stderr)
        return 2

    inbox = ImapInbox(config)
    since = (
        args.since_epoch - 30
        if args.since_epoch is not None
        else time.time() - args.since_seconds
    )
    deadline = time.time() + args.timeout
    wanted_subject = args.subject.casefold().strip()
    recipient = args.recipient
    print(
        f'waiting for Riot "{args.subject}" link on {config.user} @{config.host}'
        + (f" for {recipient}" if recipient else "")
        + "…",
        file=sys.stderr,
    )

    any_fallback_after = time.time() + min(90.0, args.timeout * 0.45)
    while time.time() < deadline:
        try:
            messages = inbox.fetch_recent_riot_mail(since_epoch=since)
        except Exception as exc:
            print(f"imap poll: {exc}", file=sys.stderr)
            messages = []

        picked = _pick_link(
            messages, wanted_subject=wanted_subject, recipient=recipient
        )
        if picked is None and recipient and time.time() >= any_fallback_after:
            # Recipient header missing on some iCloud forwards — accept newest verify link.
            picked = _pick_link(
                messages, wanted_subject=wanted_subject, recipient=""
            )
            if picked:
                print(
                    "recipient filter missed; using newest Verify Your Email link",
                    file=sys.stderr,
                )
        if picked and picked.verify_link:
            print(f'found verify link in "{picked.subject}"', file=sys.stderr)
            print(picked.verify_link)
            return 0
        time.sleep(3)

    print(f'timeout waiting for "{args.subject}" verification link', file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
