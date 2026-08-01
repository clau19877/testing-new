#!/usr/bin/env python3
"""Fetch Premium Bandai email authentication codes from iCloud IMAP."""

from __future__ import annotations

import argparse
import email
import imaplib
import json
import re
import socket
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


PLACEHOLDER_EMAILS = {"", "yourname@icloud.com"}
PLACEHOLDER_PASSWORDS = {"", "xxxx-xxxx-xxxx-xxxx"}


def explain_imap_failure(exc: BaseException, cfg: dict) -> str:
    """Turn a raw connection/login exception into an actionable message."""
    icloud = cfg.get("icloud", {})
    host = icloud.get("imap_host", "imap.mail.me.com")
    port = icloud.get("imap_port", 993)
    email_val = (icloud.get("email") or "").strip()
    pw_val = (icloud.get("app_specific_password") or "").strip()

    if email_val.lower() in PLACEHOLDER_EMAILS or pw_val in PLACEHOLDER_PASSWORDS:
        return (
            "config.json still has placeholder iCloud credentials. "
            "Edit icloud.email and icloud.app_specific_password in config.json first."
        )

    if isinstance(exc, (socket.gaierror, ConnectionRefusedError)):
        return (
            f"Could not reach {host}:{port} — check your internet connection, "
            "and that no firewall/VPN/proxy is blocking IMAP (port 993)."
        )
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return (
            f"Connection to {host}:{port} timed out — likely a firewall/VPN/proxy "
            "blocking outbound port 993."
        )
    if isinstance(exc, imaplib.IMAP4.error):
        detail = str(exc)
        if "AUTHENTICATIONFAILED" in detail.upper() or "invalid credentials" in detail.lower():
            return (
                "iCloud rejected the login. Most common causes:\n"
                "  1. Using your normal Apple ID password instead of an APP-SPECIFIC password\n"
                "     (create one at https://appleid.apple.com -> Sign-In and Security -> App-Specific Passwords)\n"
                "  2. Two-Factor Authentication is not enabled on the Apple ID (required for app-specific passwords)\n"
                "  3. A typo or stray space in icloud.email / icloud.app_specific_password in config.json\n"
                f"  Raw error: {detail}"
            )
        return f"IMAP error: {detail}"
    return f"{type(exc).__name__}: {exc}"


def connect_imap(cfg: dict) -> imaplib.IMAP4_SSL:
    """Connect for the retry loop (poll_for_code): dies immediately on
    permanent (credential/config) failures, but re-raises transient
    (network) ones so the caller can retry."""
    icloud = cfg["icloud"]
    host = icloud.get("imap_host", "imap.mail.me.com")
    port = int(icloud.get("imap_port", 993))
    try:
        client = imaplib.IMAP4_SSL(host, port, timeout=20)
        client.login(icloud["email"], icloud["app_specific_password"])
        client.select(icloud.get("mailbox", "INBOX"))
        return client
    except (imaplib.IMAP4.error, OSError, socket.timeout) as exc:
        explained = explain_imap_failure(exc, cfg)
        # Bad credentials / placeholder config won't fix themselves on retry —
        # fail fast with a clear message instead of retrying for minutes.
        permanent = isinstance(exc, imaplib.IMAP4.error) or explained.startswith("config.json still has")
        if permanent:
            die(explained, step="imap_connect", to_email=icloud.get("email", ""))
        signup_log.log_error(
            explained,
            source="fetch_icloud_code",
            step="imap_connect",
            email=icloud.get("email", ""),
        )
        raise


def connect_imap_once(cfg: dict) -> imaplib.IMAP4_SSL:
    """Connect for single-shot usage (--once, --diagnose): any failure gets
    a clear message and exits immediately — retrying doesn't make sense
    for a one-off check."""
    icloud = cfg["icloud"]
    host = icloud.get("imap_host", "imap.mail.me.com")
    port = int(icloud.get("imap_port", 993))
    try:
        client = imaplib.IMAP4_SSL(host, port, timeout=20)
        client.login(icloud["email"], icloud["app_specific_password"])
        client.select(icloud.get("mailbox", "INBOX"))
        return client
    except (imaplib.IMAP4.error, OSError, socket.timeout) as exc:
        die(explain_imap_failure(exc, cfg), step="imap_connect", to_email=icloud.get("email", ""))


def cmd_diagnose(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    icloud = cfg.get("icloud", {})
    print(f"Host: {icloud.get('imap_host', 'imap.mail.me.com')}:{icloud.get('imap_port', 993)}")
    print(f"Email: {icloud.get('email', '(not set)')}")
    print(f"Mailbox: {icloud.get('mailbox', 'INBOX')}")
    try:
        client = connect_imap_once(cfg)
    except SystemExit as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 1
    try:
        typ, data = client.search(None, "ALL")
        count = len(data[0].split()) if typ == "OK" and data and data[0] else 0
        print(f"\nOK — logged in successfully. {count} message(s) in mailbox.")
    finally:
        client.logout()
    return 0


def iter_recent_ids(client: imaplib.IMAP4_SSL, limit: int = 25) -> list[bytes]:
    typ, data = client.search(None, "ALL")
    if typ != "OK" or not data or not data[0]:
        return []
    ids = data[0].split()
    return ids[-limit:]


def extract_rfc822_bytes(data: list) -> Optional[bytes]:
    """Find the RFC822 message bytes within an IMAP FETCH response.

    imaplib's `data` shape isn't guaranteed: some servers/parses represent a
    fetched literal as a (info_line, literal_bytes) tuple; iCloud's real
    responses (confirmed live) instead return the whole message as a single
    plain bytes entry — no tuple at all. Servers can also interleave an
    unrelated untagged response (e.g. an automatic \\Seen flag update, sent
    as its own short plain-bytes entry) before or after the real content.
    Blindly indexing data[0] can grab the wrong entry in either shape.
    """
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    byte_items = [item for item in data if isinstance(item, bytes) and item]
    if byte_items:
        # The real message body is virtually always far longer than a short
        # status line like b'123 (FLAGS (\\Seen))'.
        return max(byte_items, key=len)
    return None


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
        try:
            # Some IMAP servers (confirmed on real iCloud accounts) return an
            # empty response for the legacy "(RFC822)" fetch item while the
            # modern IMAP4rev1 "(BODY.PEEK[])" works fine and returns the
            # full message without marking it \Seen.
            typ, data = client.fetch(msg_id, "(BODY.PEEK[])")
            if typ != "OK" or not data:
                continue
            raw = extract_rfc822_bytes(data)
            if raw is None:
                continue
            msg = email.message_from_bytes(raw)
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
        except (imaplib.IMAP4.error, OSError, socket.timeout):
            raise
        except Exception as exc:  # noqa: BLE001 - one bad message must not kill the whole scan
            signup_log.log_error(
                f"Skipping unparseable message {msg_id!r}: {type(exc).__name__}: {exc}",
                source="fetch_icloud_code",
                step="mailbox_scan_skip",
                email=to_email,
            )
            continue
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
        except (imaplib.IMAP4.error, OSError, socket.timeout) as exc:
            # connect_imap already dies() on permanent (credential/config)
            # failures, so anything reaching here is a transient network
            # issue worth retrying.
            last_err = exc
            signup_log.log_error(
                f"IMAP error while polling: {exc}",
                source="fetch_icloud_code",
                step="imap_poll",
                email=to_email,
            )
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass
        time.sleep(interval)

    if last_err:
        die(f"Timed out waiting for auth code. Last IMAP error: {last_err}", step="imap_timeout", to_email=to_email)
    die("Timed out waiting for Premium Bandai auth code in iCloud mail.", step="imap_timeout", to_email=to_email)


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
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Test the iCloud IMAP connection/login only and explain any failure.",
    )
    args = parser.parse_args()

    if args.diagnose:
        raise SystemExit(cmd_diagnose(args))

    cfg = load_config(args.config)

    if args.once:
        client = connect_imap_once(cfg)
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
