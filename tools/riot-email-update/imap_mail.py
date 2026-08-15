"""IMAP helper to pull Riot MFA / email-change verification codes."""

from __future__ import annotations

import email
import html
import imaplib
import os
import re
import time
from dataclasses import dataclass
from datetime import timezone
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
        r"[^\s\"'<>]*(?:verify|confirm|email-verification|email)[^\s\"'<>]*",
        re.I,
    ),
    # Riot email templates may use a tracking URL; select the href around
    # the visible "Verify Email" call-to-action.
    re.compile(
        r'href\s*=\s*["\'](https?://[^"\']+)["\'][^>]*>'
        r"(?:(?!</a>).){0,800}?(?:verify\s+(?:your\s+)?email|confirm\s+email)",
        re.I | re.S,
    ),
    # Tracking links (links.riotgames.com) near a Verify CTA.
    re.compile(
        r'href\s*=\s*["\'](https?://links\.riotgames\.com/[^"\']+)["\']',
        re.I,
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
    recipients: str = ""


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


def _recipient_blob(msg: Message, body: str) -> str:
    parts: list[str] = []
    for header in (
        "To",
        "Cc",
        "Delivered-To",
        "X-Original-To",
        "X-Forwarded-To",
        "Envelope-To",
    ):
        parts.append(_decode_mime(msg.get(header)))
    # Body often names the address being verified.
    parts.append(body[:8000])
    return "\n".join(parts).casefold()


def _message_received_epoch(msg: Message) -> float:
    raw_date = msg.get("Date") or ""
    try:
        date_tuple = email.utils.parsedate_to_datetime(raw_date)
        if date_tuple.tzinfo is None:
            date_tuple = date_tuple.replace(tzinfo=timezone.utc)
        return date_tuple.timestamp()
    except Exception:
        return time.time()


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


def _imap_quote_mailbox(name: str) -> str:
    """Quote mailbox names safely for SELECT (spaces / specials)."""
    cleaned = (name or "INBOX").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{cleaned}"'


class ImapInbox:
    def __init__(self, config: ImapConfig):
        self.config = config

    def _login(self) -> imaplib.IMAP4:
        # Socket timeout so a stalled IMAP server cannot pin the Safari helper forever.
        try:
            sock_timeout = float(os.getenv("IMAP_SOCKET_TIMEOUT") or "20")
        except Exception:
            sock_timeout = 20.0
        if self.config.use_ssl:
            client: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                self.config.host, self.config.port, timeout=sock_timeout
            )
        else:
            client = imaplib.IMAP4(
                self.config.host, self.config.port, timeout=sock_timeout
            )
        client.login(self.config.user, self.config.password)
        return client

    def _connect(self) -> imaplib.IMAP4:
        client = self._login()
        typ, _ = client.select(_imap_quote_mailbox(self.config.folder))
        if typ != "OK":
            try:
                client.logout()
            except Exception:
                pass
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
        # Prefer subject matches. Never let a broad FROM search push Verify UIDs
        # out of the candidate window (old bug: merged[-60:] dropped them).
        subject_queries = [
            '(SUBJECT "Verify Your Email")',
            '(SUBJECT "Verify Email")',
            '(SUBJECT "Verify")',
        ]
        broad_queries = [
            '(FROM "riotgames.com")',
            '(FROM "riotgames")',
            '(FROM "riot")',
        ]
        subject_uids: list[bytes] = []
        broad_uids: list[bytes] = []
        seen: set[bytes] = set()

        def _add(target: list[bytes], query: str) -> None:
            typ, data = client.uid("search", None, query)
            if typ != "OK" or not data or not data[0]:
                return
            for uid in data[0].split():
                if uid in seen:
                    continue
                seen.add(uid)
                target.append(uid)

        for query in subject_queries:
            _add(subject_uids, query)
        for query in broad_queries:
            _add(broad_uids, query)

        def _newest(uids: list[bytes], limit: int) -> list[bytes]:
            if not uids:
                return []
            try:
                return sorted(uids, key=lambda u: int(u))[-limit:]
            except Exception:
                return uids[-limit:]

        # Keep all recent subject hits, plus newest broad hits as backup.
        preferred = _newest(subject_uids, 80)
        if len(preferred) >= 10:
            return preferred
        backup = _newest(broad_uids, 40)
        merged = preferred + [u for u in backup if u not in set(preferred)]
        if merged:
            return _newest(merged, 100)
        typ, data = client.uid("search", None, "ALL")
        if typ == "OK" and data and data[0]:
            return _newest(data[0].split(), 40)
        return []

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

    def _list_mailbox_names(self, client: imaplib.IMAP4) -> list[str]:
        """Return mailbox names from LIST (unquoted)."""
        names: list[str] = []
        try:
            typ, data = client.list()
        except Exception:
            return names
        if typ != "OK" or not data:
            return names
        for item in data:
            if not isinstance(item, (bytes, bytearray, str)):
                continue
            line = item.decode() if isinstance(item, (bytes, bytearray)) else item
            # Typical: (\Junk) "/" "Junk"  or  () "/" INBOX
            match = re.search(r' "((?:\\.|[^"\\])*)"$', line)
            if match:
                raw_name = match.group(1).replace('\\"', '"').replace("\\\\", "\\")
            else:
                match = re.search(r" (\S+)$", line)
                if not match:
                    continue
                raw_name = match.group(1)
            if raw_name:
                names.append(raw_name)
        return names

    def _iter_folders(self, client: imaplib.IMAP4 | None = None) -> list[str]:
        # iCloud sometimes files Riot mail into Junk. Prefer LIST-discovered
        # folders so we never SELECT a non-existent "Junk Folder" (unquoted
        # names with spaces cause BAD Parse Error and used to abort the fetch).
        primary = self.config.folder or "INBOX"
        out: list[str] = [primary]
        seen = {primary.casefold()}
        listed: list[str] = []
        if client is not None:
            listed = self._list_mailbox_names(client)
        junk_like = []
        for name in listed:
            lower = name.casefold()
            if lower in seen:
                continue
            if any(token in lower for token in ("junk", "spam", "bulk")):
                junk_like.append(name)
                seen.add(lower)
        # Only guess common junk names when LIST returned nothing junk-like.
        # Trying many missing mailboxes costs a full round-trip each poll.
        if not junk_like and client is not None and not listed:
            for name in ("Junk", "Spam"):
                if name.casefold() not in seen:
                    junk_like.append(name)
                    seen.add(name.casefold())
        out.extend(junk_like)
        return out

    def _fetch_folder_mails(
        self,
        client: imaplib.IMAP4,
        folder: str,
        *,
        since_epoch: float,
        min_received: float,
        seen_uids: set[str],
        found: list[RiotMail],
    ) -> None:
        try:
            typ, _ = client.select(_imap_quote_mailbox(folder))
        except Exception:
            return
        if typ != "OK":
            return
        for uid in self._search_uids(client, since_epoch):
            raw = self._fetch_message_bytes(client, uid)
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            sender = _decode_mime(msg.get("From"))
            subject = _decode_mime(msg.get("Subject"))
            if not _is_riot_mail(sender, subject):
                continue
            received = _message_received_epoch(msg)
            if received < min_received:
                continue
            body = _body_text(msg)
            blob = f"{subject}\n{body}"
            uid_s = uid.decode() if isinstance(uid, bytes) else str(uid)
            key = f"{folder}:{uid_s}"
            if key in seen_uids:
                continue
            seen_uids.add(key)
            found.append(
                RiotMail(
                    uid=uid_s,
                    subject=subject,
                    sender=sender,
                    code=_extract_code(blob),
                    verify_link=_extract_verify_link(blob),
                    received_epoch=received,
                    recipients=_recipient_blob(msg, body),
                )
            )

    def fetch_recent_riot_mail(
        self,
        *,
        since_epoch: float,
        client: imaplib.IMAP4 | None = None,
        folders: list[str] | None = None,
        inbox_only: bool = False,
    ) -> list[RiotMail]:
        """
        Fetch recent Riot mail. Pass an open `client` to avoid re-login every poll.
        `inbox_only=True` skips junk/spam folders (faster early polls).
        """
        found: list[RiotMail] = []
        seen_uids: set[str] = set()
        # Allow modest clock skew between Mac and IMAP Date headers.
        min_received = since_epoch - 120
        owns_client = client is None
        login_error: Exception | None = None

        try:
            if client is None:
                client = self._login()
        except Exception as exc:
            raise exc

        assert client is not None
        try:
            if folders is None:
                if inbox_only:
                    folders = [self.config.folder or "INBOX"]
                else:
                    folders = self._iter_folders(client)
            for folder in folders:
                try:
                    self._fetch_folder_mails(
                        client,
                        folder,
                        since_epoch=since_epoch,
                        min_received=min_received,
                        seen_uids=seen_uids,
                        found=found,
                    )
                except Exception:
                    # Keep polling other folders; connection may still be usable.
                    continue
        except Exception as exc:
            login_error = exc
        finally:
            if owns_client and client is not None:
                try:
                    client.logout()
                except Exception:
                    pass
        if login_error is not None and not found:
            raise login_error
        found.sort(key=lambda m: m.received_epoch, reverse=True)
        return found

    def wait_for_code(
        self,
        *,
        since_epoch: float,
        timeout: float = 120.0,
        poll_interval: float = 2.0,
        used_codes: Iterable[str] | None = None,
    ) -> str:
        used = set(used_codes or [])
        deadline = time.time() + timeout
        print(
            f"  IMAP: waiting for Riot code in {self.config.user} "
            f"(timeout {timeout:.0f}s)…"
        )
        client = None
        folders: list[str] | None = None
        try:
            client = self._login()
            folders = self._iter_folders(client)
            started = time.time()
            while time.time() < deadline:
                use_folders = folders[:1] if (time.time() - started) < 12.0 else folders
                mails = self.fetch_recent_riot_mail(
                    since_epoch=since_epoch,
                    client=client,
                    folders=use_folders,
                )
                for mail in mails:
                    if mail.code and mail.code not in used:
                        print(f"  IMAP: found code in “{mail.subject}”")
                        return mail.code
                time.sleep(poll_interval)
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass
        raise TimeoutError(
            f"No Riot verification code in {self.config.user} within {timeout:.0f}s"
        )

    def wait_for_verify_link(
        self,
        *,
        since_epoch: float,
        timeout: float = 90.0,
        poll_interval: float = 1.5,
    ) -> str:
        deadline = time.time() + timeout
        print(
            f"  IMAP: waiting for Riot verify link in {self.config.user} "
            f"(timeout {timeout:.0f}s)…"
        )
        client = None
        folders: list[str] | None = None
        try:
            client = self._login()
            folders = self._iter_folders(client)
            started = time.time()
            while time.time() < deadline:
                use_folders = folders[:1] if (time.time() - started) < 15.0 else folders
                mails = self.fetch_recent_riot_mail(
                    since_epoch=since_epoch,
                    client=client,
                    folders=use_folders,
                )
                for mail in mails:
                    if mail.verify_link:
                        print(f"  IMAP: found verify link in “{mail.subject}”")
                        return mail.verify_link
                time.sleep(poll_interval)
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass
        raise TimeoutError(
            f"No Riot verify link in {self.config.user} within {timeout:.0f}s"
        )
