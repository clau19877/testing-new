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


def _is_verify_candidate(message, *, wanted_subject: str) -> bool:
    subject = (message.subject or "").casefold()
    if wanted_subject in subject:
        return bool(message.verify_link)
    if "verify" in subject:
        return bool(message.verify_link)
    return False


def _pick_link(
    messages,
    *,
    wanted_subject: str,
    recipient: str,
    allow_any: bool = False,
    fallback_min_received: float | None = None,
):
    """Prefer newest verify mail that mentions recipient; optional timed fallback."""
    recip = (recipient or "").casefold().strip()
    matched = []
    fallback = []
    for message in messages:
        if not _is_verify_candidate(message, wanted_subject=wanted_subject):
            continue
        if recip and recip in (message.recipients or ""):
            matched.append(message)
            continue
        if fallback_min_received is not None and message.received_epoch < fallback_min_received:
            continue
        fallback.append(message)
    if matched:
        return matched[0], "recipient"
    if not allow_any or not fallback:
        return None, ""
    if len(fallback) == 1:
        return fallback[0], "only-fresh"
    return fallback[0], "newest-fresh"


def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("IMAP_VERIFY_TIMEOUT") or "120"),
    )
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
    parser.add_argument(
        "--poll",
        type=float,
        default=float(os.getenv("IMAP_POLL_INTERVAL") or "1.5"),
        help="Seconds between IMAP polls (default 1.5)",
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
    # Fallback must stay near the SAVE click — do not reuse older verify mails.
    fallback_min_received = (
        float(args.since_epoch) - 15.0
        if args.since_epoch is not None
        else since
    )
    deadline = time.time() + args.timeout
    wanted_subject = args.subject.casefold().strip()
    recipient = args.recipient
    poll = max(0.75, float(args.poll))
    print(
        f'waiting for Riot "{args.subject}" link on {config.user} @{config.host}'
        + (f" for {recipient}" if recipient else "")
        + f" (timeout {args.timeout:.0f}s, poll {poll:.1f}s)…",
        file=sys.stderr,
    )

    # After a short recipient-only window, allow a timed fallback.
    any_fallback_after = time.time() + min(18.0, max(10.0, args.timeout * 0.15))
    last_diag = ""
    client = None
    folders = None
    started = time.time()
    try:
        client = inbox._login()
        folders = inbox._iter_folders(client)
        while time.time() < deadline:
            try:
                use_folders = folders[:1] if (time.time() - started) < 15.0 else folders
                messages = inbox.fetch_recent_riot_mail(
                    since_epoch=since,
                    client=client,
                    folders=use_folders,
                )
            except Exception as exc:
                print(f"imap poll: {exc}", file=sys.stderr)
                messages = []
                # Reconnect once after a broken session.
                try:
                    if client is not None:
                        try:
                            client.logout()
                        except Exception:
                            pass
                    client = inbox._login()
                    folders = inbox._iter_folders(client)
                except Exception as reconnect_exc:
                    print(f"imap reconnect: {reconnect_exc}", file=sys.stderr)
                    time.sleep(poll)
                    continue

            verify_msgs = [
                m
                for m in messages
                if "verify" in (m.subject or "").casefold() or m.verify_link
            ]
            diag = (
                f"imap seen={len(messages)} verifyish={len(verify_msgs)} "
                f"with_link={sum(1 for m in verify_msgs if m.verify_link)}"
            )
            if diag != last_diag:
                print(diag, file=sys.stderr)
                last_diag = diag

            allow_any = (not recipient) or (time.time() >= any_fallback_after)
            picked, how = _pick_link(
                messages,
                wanted_subject=wanted_subject,
                recipient=recipient,
                allow_any=allow_any,
                fallback_min_received=fallback_min_received if recipient else None,
            )
            if picked and picked.verify_link:
                if how and how != "recipient":
                    print(
                        f"recipient filter missed; using {how} Verify Your Email link",
                        file=sys.stderr,
                    )
                print(f'found verify link in "{picked.subject}"', file=sys.stderr)
                print(picked.verify_link)
                return 0
            time.sleep(poll)
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass

    print(f'timeout waiting for "{args.subject}" verification link', file=sys.stderr)
    if last_diag:
        print(f"last status: {last_diag}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
