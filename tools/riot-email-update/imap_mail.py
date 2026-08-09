"""IMAP helper to pull Riot MFA / email-change verification codes."""

from __future__ import annotations

import email
import html
import imaplib
import re
import time
from dataclasses import dataclass
from email.header import decode_header
from email.message import Message
from typing import Iterable


RIOT_FROM_HINTS = (
    "riotgames.com",
    "email.accounts.riotgames.com",
    "noreply@riotgames.com",
    "leagueoflegends@",
    # iCloud often rewrites Riot's From into a Hide-My-Email style address:
    #   Riot Games <noreply_at_umail_accounts_riotgames_com_…@icloud.com>
    "riotgames_com",
    "umail_accounts_riotgames",
    "riot games",
)

CODE_PATTERNS = (
    re.compile(r"\b(\d{6})\b"),
    re.compile(r"(?:code|passcode|verification)[^\d]{0,40}(\d{6})", re.I),
)

VERIFY_LINK_PATTERNS = (
    # Prefer a link whose URL itself clearly identifies the verification action.
    re.compile(
        r"https?://[^\s\"'<>]*(?:riotgames\.com|riotgames\.com\.cn)"
        r"[^\s\"'<>]*(?:verify|confirm|email)[^\s\"'<>]*",
        re.I,
    ),
    # Riot email templates may use a tracking URL; select the href around
    # the visible "Verify Email" call-to-action.
    re.compile(
        r'href\s*=\s*["\'](https?://[^"\']+)["\'][^>]*>'
        r"(?:(?!</a>).){0,500}?(?:verify\s+(?:your\s+)?email|confirm\s+email)",
        re.I | re.S,
    ),
)


@dataclass
class ImapConfig:
    host: str
    user: str
    password: str
    port: int = 993
    folder: str = "INBOX"
    use_ssl: bool = True

    @classmethod
    def from_env(cls, env: dict[str, str], prefix: str = "IMAP") -> "ImapConfig | None":
        host = (env.get(f"{prefix}_HOST") or "").strip()
        user = (env.get(f"{prefix}_USER") or "").strip()
        password = (env.get(f"{prefix}_PASSWORD") or "").strip()
        if not (host and user and password):
            return None
        port_raw = (env.get(f"{prefix}_PORT") or "993").strip()
        folder = (env.get(f"{prefix}_FOLDER") or "INBOX").strip() or "INBOX"
        return cls(
            host=host,
            user=user,
            password=password,
            port=int(port_raw or "993"),
            folder=folder,
            use_ssl=(env.get(f"{prefix}_SSL") or "true").strip().lower()
            not in {"0", "false", "no"},
        )


@dataclass
class RiotMail:
    uid: str
    subject: str
    sender: str
    code: str | None
    verify_link: str | None
    received_epoch: float


def _decode_mime(value: str | None) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for chunk, charset in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


def _body_text(msg: Message) -> str:
    chunks: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            if ctype in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                chunks.append(payload.decode(charset, errors="replace"))
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        chunks.append(payload.decode(charset, errors="replace"))
    return "\n".join(chunks)


def _extract_code(text: str) -> str | None:
    # Prefer patterns near "code" wording; fall back to any 6-digit token.
    for pattern in CODE_PATTERNS[1:]:
        m = pattern.search(text)
        if m:
            return m.group(1)
    m = CODE_PATTERNS[0].search(text)
    return m.group(1) if m else None


def _extract_verify_link(text: str) -> str | None:
    decoded = html.unescape(text)
    for pattern in VERIFY_LINK_PATTERNS:
        match = pattern.search(decoded)
        if not match:
            continue
        url = match.group(1) if match.lastindex else match.group(0)
        return html.unescape(url).rstrip(").,]}>\"'")
    return None


def _is_riot_sender(sender: str) -> bool:
    lower = sender.lower()
    if any(hint in lower for hint in RIOT_FROM_HINTS):
        return True
    # Broad fallback: mangled local-parts still contain "riotgames".
    return "riotgames" in lower


def _is_riot_mail(sender: str, subject: str) -> bool:
    subj = (subject or "").lower()
    if _is_riot_sender(sender):
        return True
    if "riot" in subj:
        return True
    # Email-change messages are titled exactly this; iCloud may rewrite From.
    if "verify your email" in subj or "verify email" in subj:
        return True
    return False


def _raw_from_fetch(data) -> bytes | None:
    """Extract message bytes from imaplib uid fetch response tuples."""
    if not data:
        return None
    for item in data:
        if not isinstance(item, tuple):
            continue
        for part in item:
            if isinstance(part, (bytes, bytearray)) and len(part) > 200:
                return bytes(part)
    return None


class ImapInbox:
    def __init__(self, config: ImapConfig):
        self.config = config

    def _connect(self) -> imaplib.IMAP4:
        if self.config.use_ssl:
            client: imaplib.IMAP4 = imaplib.IMAP4_SSL(self.config.host, self.config.port)
        else:
            client = imaplib.IMAP4(self.config.host, self.config.port)
        client.login(self.config.user, self.config.password)
        typ, _ = client.select(self.config.folder)
        if typ != "OK":
            client.logout()
            raise RuntimeError(f"Cannot select folder {self.config.folder!r}")
        return client

    def test_connection(self) -> None:
        client = self._connect()
        try:
            client.noop()
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def _search_uids(self, client: imaplib.IMAP4, since_epoch: float) -> list[bytes]:
        # Merge several provider-specific searches; iCloud often misses FROM riotgames.com
        # because it rewrites the sender to @icloud.com.
        queries = [
            '(SUBJECT "Verify Your Email")',
            '(SUBJECT "Verify")',
            '(FROM "riotgames.com")',
            '(FROM "riot")',
            '(FROM "riotgames")',
        ]
        merged: list[bytes] = []
        seen: set[bytes] = set()
        for query in queries:
            typ, data = client.uid("search", None, query)
            if typ != "OK" or not data or not data[0]:
                continue
            for uid in data[0].split():
                if uid not in seen:
                    seen.add(uid)
                    merged.append(uid)
        if not merged:
            typ, data = client.uid("search", None, "ALL")
            if typ == "OK" and data and data[0]:
                merged = data[0].split()
        # Keep the newest ~60 candidates (verify mail may not be the absolute newest).
        return merged[-60:]

    def _fetch_message_bytes(self, client: imaplib.IMAP4, uid: bytes) -> bytes | None:
        # iCloud frequently returns an empty stub for UID FETCH RFC822; BODY.PEEK[] works.
        for spec in ("(BODY.PEEK[])", "(RFC822)", "(BODY[])"):
            try:
                typ, data = client.uid("fetch", uid, spec)
            except Exception:
                continue
            if typ != "OK":
                continue
            raw = _raw_from_fetch(data)
            if raw:
                return raw
        return None

    def fetch_recent_riot_mail(self, *, since_epoch: float) -> list[RiotMail]:
        client = self._connect()
        found: list[RiotMail] = []
        try:
            for uid in self._search_uids(client, since_epoch):
                raw = self._fetch_message_bytes(client, uid)
                if not raw:
                    continue
                msg = email.message_from_bytes(raw)
                sender = _decode_mime(msg.get("From"))
                subject = _decode_mime(msg.get("Subject"))
                if not _is_riot_mail(sender, subject):
                    continue
                date_tuple = email.utils.parsedate_to_datetime(msg.get("Date") or "")
                try:
                    received = date_tuple.timestamp()
                except Exception:
                    received = time.time()
                if received + 5 < since_epoch:
                    continue
                body = _body_text(msg)
                blob = f"{subject}\n{body}"
                found.append(
                    RiotMail(
                        uid=uid.decode() if isinstance(uid, bytes) else str(uid),
                        subject=subject,
                        sender=sender,
                        code=_extract_code(blob),
                        verify_link=_extract_verify_link(blob),
                        received_epoch=received,
                    )
                )
        finally:
            try:
                client.logout()
            except Exception:
                pass
        found.sort(key=lambda m: m.received_epoch, reverse=True)
        return found

    def wait_for_code(
        self,
        *,
        since_epoch: float,
        timeout: float = 180.0,
        poll_interval: float = 5.0,
        used_codes: Iterable[str] | None = None,
    ) -> str:
        used = set(used_codes or [])
        deadline = time.time() + timeout
        print(
            f"  IMAP: waiting for Riot code in {self.config.user} "
            f"(timeout {timeout:.0f}s)…"
        )
        while time.time() < deadline:
            mails = self.fetch_recent_riot_mail(since_epoch=since_epoch)
            for mail in mails:
                if mail.code and mail.code not in used:
                    print(f"  IMAP: found code in “{mail.subject}”")
                    return mail.code
            time.sleep(poll_interval)
        raise TimeoutError(
            f"No Riot verification code in {self.config.user} within {timeout:.0f}s"
        )

    def wait_for_verify_link(
        self,
        *,
        since_epoch: float,
        timeout: float = 180.0,
        poll_interval: float = 5.0,
    ) -> str:
        deadline = time.time() + timeout
        print(
            f"  IMAP: waiting for Riot verify link in {self.config.user} "
            f"(timeout {timeout:.0f}s)…"
        )
        while time.time() < deadline:
            mails = self.fetch_recent_riot_mail(since_epoch=since_epoch)
            for mail in mails:
                if mail.verify_link:
                    print(f"  IMAP: found verify link in “{mail.subject}”")
                    return mail.verify_link
                # Some flows only include a code; caller can fall back.
            time.sleep(poll_interval)
        raise TimeoutError(
            f"No Riot verify link in {self.config.user} within {timeout:.0f}s"
        )
