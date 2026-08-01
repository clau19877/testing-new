#!/usr/bin/env python3
"""Fetch Premium Bandai email authentication codes from iCloud IMAP."""

from __future__ import annotations

import argparse
import email
import imaplib
import json
import re
import sys
import time
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable, Optional

import signup_log


DEFAULT_CONFIG = Path(__file__).with_name("config.json")


def die(message: str, *, step: str = "icloud", to_email: str = "") -> None:
    signup_log.log_error(message, source="fetch_icloud_code", step=step, email=to_email)
    raise SystemExit(message)


def load_config(path: Path) -> dict:
    if not path.exists():
        die(
            f"Missing config: {path}. Copy config.example.json to config.json and fill in your iCloud app password.",
            step="load_config",
        )
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def decode_mime(value: Optional[str]) -> str:
    if not value:
        return ""
    parts = []
    for chunk, enc in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


def message_body(msg: email.message.Message) -> str:
    texts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if ctype in ("text/plain", "text/html") and "attachment" not in disp.lower():
                raw = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                texts.append(raw.decode(charset, errors="replace"))
    else:
        raw = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        texts.append(raw.decode(charset, errors="replace"))
    return "\n".join(texts)


def strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def looks_relevant(subject: str, sender: str, subject_hints: Iterable[str], from_hints: Iterable[str]) -> bool:
    hay_sub = subject.lower()
    hay_from = sender.lower()
    if any(h.lower() in hay_sub for h in subject_hints):
        return True
    if any(h.lower() in hay_from for h in from_hints) and any(
        k in hay_sub for k in ("code", "auth", "verif", "confirm", "premium", "bandai")
    ):
        return True
    return False


def mentions_recipient(msg: email.message.Message, body: str, to_email: str) -> bool:
    if not to_email:
        return True
    target = to_email.strip().lower()
    headers = " ".join(
        [
            decode_mime(msg.get("To")),
            decode_mime(msg.get("Cc")),
            decode_mime(msg.get("Delivered-To")),
            decode_mime(msg.get("X-Original-To")),
        ]
    ).lower()
    if target in headers:
        return True
    # Plus-alias / body mentions
    return target in body.lower()


def extract_code(text: str, pattern: str) -> Optional[str]:
    # Prefer explicit "code is 123456" style matches first.
    labeled = re.search(
        r"(?i)(?:authentication|verification|auth|security)?\s*code(?:\s*is)?\s*[:\-]?\s*(\d{4,8})",
        text,
    )
    if labeled:
        return labeled.group(1)
    matches = re.findall(pattern, text)
    if not matches:
        return None
    # Prefer 6-digit codes common to Bandai flows.
    six = [m for m in matches if len(m) == 6]
    if six:
        return six[0]
    return matches[0]


def connect_imap(cfg: dict) -> imaplib.IMAP4_SSL:
    icloud = cfg["icloud"]
    client = imaplib.IMAP4_SSL(icloud.get("imap_host", "imap.mail.me.com"), int(icloud.get("imap_port", 993)))
    client.login(icloud["email"], icloud["app_specific_password"])
    client.select(icloud.get("mailbox", "INBOX"))
    return client


def iter_recent_ids(client: imaplib.IMAP4_SSL, limit: int = 25) -> list[bytes]:
    typ, data = client.search(None, "ALL")
    if typ != "OK" or not data or not data[0]:
        return []
    ids = data[0].split()
    return ids[-limit:]


def find_code_in_mailbox(
    client: imaplib.IMAP4_SSL,
    cfg: dict,
    not_before_epoch: float,
    to_email: str = "",
) -> Optional[str]:
    poll = cfg.get("code_poll", {})
    subject_hints = poll.get("subject_hints", [])
    from_hints = poll.get("from_hints", [])
    pattern = poll.get("code_regex", r"\b(\d{4,8})\b")

    for msg_id in reversed(iter_recent_ids(client)):
        typ, data = client.fetch(msg_id, "(RFC822)")
        if typ != "OK" or not data or not data[0]:
            continue
        msg = email.message_from_bytes(data[0][1])
        subject = decode_mime(msg.get("Subject"))
        sender = decode_mime(msg.get("From"))
        date_hdr = msg.get("Date")
        if date_hdr:
            try:
                if parsedate_to_datetime(date_hdr).timestamp() < not_before_epoch - 30:
                    continue
            except (TypeError, ValueError, IndexError, OverflowError):
                pass
        if not looks_relevant(subject, sender, subject_hints, from_hints):
            continue
        body = strip_html(message_body(msg))
        if not mentions_recipient(msg, body, to_email):
            continue
        code = extract_code(f"{subject}\n{body}", pattern)
        if code:
            return code
    return None


def poll_for_code(
    cfg: dict,
    not_before_epoch: Optional[float] = None,
    to_email: str = "",
) -> str:
    poll = cfg.get("code_poll", {})
    timeout = int(poll.get("timeout_sec", 540))
    interval = int(poll.get("interval_sec", 8))
    started = time.time()
    cutoff = not_before_epoch if not_before_epoch is not None else started - 60

    last_err = None
    while time.time() - started < timeout:
        client = None
        try:
            client = connect_imap(cfg)
            code = find_code_in_mailbox(client, cfg, cutoff, to_email=to_email)
            if code:
                return code
        except imaplib.IMAP4.error as exc:
            last_err = exc
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass
        time.sleep(interval)

    if last_err:
        raise SystemExit(f"Timed out waiting for auth code. Last IMAP error: {last_err}")
    raise SystemExit("Timed out waiting for Premium Bandai auth code in iCloud mail.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll iCloud IMAP for a Premium Bandai auth code.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--since-epoch",
        type=float,
        default=None,
        help="Only consider messages at/after this UNIX timestamp.",
    )
    parser.add_argument(
        "--to-email",
        default="",
        help="Prefer messages addressed to this signup email.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Single mailbox scan (no polling loop).",
    )
    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.once:
        client = connect_imap(cfg)
        try:
            code = find_code_in_mailbox(
                client,
                cfg,
                args.since_epoch or (time.time() - 600),
                to_email=args.to_email,
            )
        finally:
            client.logout()
        if not code:
            raise SystemExit(2)
        print(code)
        return

    print(poll_for_code(cfg, args.since_epoch, to_email=args.to_email))


if __name__ == "__main__":
    main()
