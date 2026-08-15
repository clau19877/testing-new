#!/usr/bin/env python3
"""Unit tests for Riot verify-link parsing (no network)."""

from __future__ import annotations

from types import SimpleNamespace

from fetch_riot_verify_link import _pick_link
from imap_mail import (
    _extract_verify_link,
    _imap_quote_mailbox,
    _is_riot_mail,
    _is_riot_sender,
)


def test_icloud_mangled_sender() -> None:
    sender = (
        "Riot Games <noreply_at_umail_accounts_riotgames_com_"
        "gfea66e1j969fk_6e8d0597@icloud.com>"
    )
    assert _is_riot_sender(sender)
    assert _is_riot_mail(sender, "Verify Your Email")


def test_verify_subject_without_riot_in_from() -> None:
    assert _is_riot_mail("someone@icloud.com", "Verify Your Email")


def test_extract_ap_account_verify_link() -> None:
    html = """
    <a href="https://links.riotgames.com/track/abc">ignore</a>
    <a href="https://ap.account.riotgames.com/email-verification?token=abc123&amp;locale=en">
      Verify Email
    </a>
    """
    link = _extract_verify_link(html)
    assert link is not None
    assert "ap.account.riotgames.com" in link
    assert "token=abc123" in link


def test_imap_quote_mailbox_spaces() -> None:
    assert _imap_quote_mailbox("Junk Folder") == '"Junk Folder"'
    assert _imap_quote_mailbox("INBOX") == '"INBOX"'


def test_pick_link_prefers_hide_my_email_recipient() -> None:
    messages = [
        SimpleNamespace(
            subject="Verify Your Email",
            verify_link="https://ap.account.riotgames.com/email-verification/old",
            recipients="hide my email <other@icloud.com>",
            received_epoch=200.0,
        ),
        SimpleNamespace(
            subject="Verify Your Email",
            verify_link="https://ap.account.riotgames.com/email-verification/new",
            recipients="hide my email <alias@icloud.com>\nbody",
            received_epoch=100.0,
        ),
    ]
    # fetch_recent_riot_mail sorts newest-first; keep that order here.
    messages.sort(key=lambda m: m.received_epoch, reverse=True)
    picked, how = _pick_link(
        messages,
        wanted_subject="verify your email",
        recipient="alias@icloud.com",
    )
    assert how == "recipient"
    assert picked is not None
    assert picked.verify_link.endswith("/new")


def test_pick_link_fallback_requires_fresh() -> None:
    messages = [
        SimpleNamespace(
            subject="Verify Your Email",
            verify_link="https://ap.account.riotgames.com/email-verification/old",
            recipients="hide my email <other@icloud.com>",
            received_epoch=50.0,
        ),
    ]
    picked, how = _pick_link(
        messages,
        wanted_subject="verify your email",
        recipient="alias@icloud.com",
        allow_any=True,
        fallback_min_received=100.0,
    )
    assert picked is None
    assert how == ""


if __name__ == "__main__":
    test_icloud_mangled_sender()
    test_verify_subject_without_riot_in_from()
    test_extract_ap_account_verify_link()
    test_imap_quote_mailbox_spaces()
    test_pick_link_prefers_hide_my_email_recipient()
    test_pick_link_fallback_requires_fresh()
    print("ok")
