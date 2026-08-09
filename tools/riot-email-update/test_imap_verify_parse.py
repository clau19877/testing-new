#!/usr/bin/env python3
"""Unit tests for Riot verify-link parsing (no network)."""

from __future__ import annotations

from imap_mail import _extract_verify_link, _is_riot_mail, _is_riot_sender


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


if __name__ == "__main__":
    test_icloud_mangled_sender()
    test_verify_subject_without_riot_in_from()
    test_extract_ap_account_verify_link()
    print("ok")
