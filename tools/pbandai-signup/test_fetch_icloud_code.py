#!/usr/bin/env python3
import email
import imaplib
import socket
import unittest

from fetch_icloud_code import (
    decode_mime,
    explain_imap_failure,
    extract_code,
    extract_rfc822_bytes,
    looks_relevant,
    mentions_recipient,
    message_body,
    strip_html,
)


class FetchCodeTests(unittest.TestCase):
    def test_extract_labeled_code(self):
        text = "Your authentication code is: 482913 Thank you."
        self.assertEqual(extract_code(text, r"\b(\d{4,8})\b"), "482913")

    def test_prefer_six_digit(self):
        text = "Ref 1234 and code 998877 for login"
        self.assertEqual(extract_code(text, r"\b(\d{4,8})\b"), "998877")

    def test_relevant_subject(self):
        self.assertTrue(
            looks_relevant(
                "PREMIUM BANDAI authentication code",
                "noreply@example.com",
                ["PREMIUM BANDAI"],
                ["p-bandai"],
            )
        )

    def test_strip_html(self):
        html = "<html><body><p>Code <b>123456</b></p></body></html>"
        self.assertIn("123456", strip_html(html))

    def test_placeholder_credentials_detected(self):
        cfg = {"icloud": {"email": "yourname@icloud.com", "app_specific_password": "xxxx-xxxx-xxxx-xxxx"}}
        msg = explain_imap_failure(OSError("boom"), cfg)
        self.assertIn("placeholder", msg)

    def test_auth_failure_message_mentions_app_specific_password(self):
        cfg = {"icloud": {"email": "me@icloud.com", "app_specific_password": "abcd-efgh-ijkl-mnop"}}
        exc = imaplib.IMAP4.error("b'[AUTHENTICATIONFAILED] Invalid credentials'")
        msg = explain_imap_failure(exc, cfg)
        self.assertIn("APP-SPECIFIC", msg)
        self.assertIn("appleid.apple.com", msg)

    def test_network_failure_message_mentions_host(self):
        cfg = {"icloud": {"email": "me@icloud.com", "app_specific_password": "abcd-efgh-ijkl-mnop", "imap_host": "imap.mail.me.com"}}
        msg = explain_imap_failure(socket.gaierror("no address"), cfg)
        self.assertIn("imap.mail.me.com", msg)
        self.assertIn("firewall", msg.lower())

    def test_real_premium_bandai_email_with_hide_my_email_relay(self):
        # Real sample: sent through an iCloud Hide My Email alias, with a
        # blockquote-style ("> ") plain-text body — both are handled fine.
        raw = (
            "From: PREMIUM BANDAI USA "
            "<info-us_at_p-bandai_com_5nv79m7jx68054_a2ad75c7@icloud.com>\n"
            'To: "Hide My Email"<search.rein-7t@icloud.com>\n'
            "Date: Sat, 01 Aug 2026 14:36:39 +0800\n"
            "Subject: [PREMIUM BANDAI] Temporary Member Registration Complete\n"
            "Content-Type: text/plain; charset=utf-8\n"
            "\n"
            " > Thank you very much for using PREMIUM BANDAI.\n"
            " > \n"
            " > Temporary member registration complete.\n"
            " > \n"
            " >  Authentication Code\n"
            " > \n"
            " > 387402\n"
            " > \n"
            " > This Authentication Code will be valid for 10 minutes.\n"
        )
        msg = email.message_from_string(raw)
        subject = decode_mime(msg.get("Subject"))
        sender = decode_mime(msg.get("From"))

        self.assertTrue(
            looks_relevant(
                subject,
                sender,
                ["PREMIUM BANDAI", "authentication", "verification"],
                ["p-bandai", "bandai", "noreply"],
            )
        )

        body = strip_html(message_body(msg))
        self.assertTrue(mentions_recipient(msg, body, "search.rein-7t@icloud.com"))
        self.assertFalse(mentions_recipient(msg, body, "someoneelse@icloud.com"))

        code = extract_code(f"{subject}\n{body}", r"\b(\d{4,8})\b")
        self.assertEqual(code, "387402")

    def test_extract_rfc822_bytes_normal_order(self):
        data = [(b"123 (RFC822 {42}", b"raw message bytes"), b")"]
        self.assertEqual(extract_rfc822_bytes(data), b"raw message bytes")

    def test_extract_rfc822_bytes_with_interleaved_flags_update(self):
        # Reproduces a real crash: an automatic \Seen flag update sent as its
        # own plain-bytes entry ordered BEFORE the literal. Naively indexing
        # data[0][1] would index into these bytes and return an int, causing
        # AttributeError: 'int' object has no attribute 'decode'.
        data = [b"123 (FLAGS (\\Seen))", (b"123 (RFC822 {42}", b"raw message bytes")]
        self.assertEqual(extract_rfc822_bytes(data), b"raw message bytes")

    def test_extract_rfc822_bytes_plain_bytes_no_tuple(self):
        # Real iCloud IMAP responses (confirmed live against a real mailbox)
        # return the whole message as a single plain bytes entry, not a
        # tuple at all. The original bug indexed data[0][1] assuming a
        # tuple, which on real bytes returns an int (AttributeError on
        # .decode()) instead of the message content.
        data = [b"From: a@b.com\r\nSubject: hi\r\n\r\nbody"]
        self.assertEqual(extract_rfc822_bytes(data), b"From: a@b.com\r\nSubject: hi\r\n\r\nbody")

    def test_extract_rfc822_bytes_prefers_longest_when_no_tuple(self):
        data = [b"123 (FLAGS (\\Seen))", b"From: a@b.com\r\nSubject: hi\r\n\r\nbody, much longer than the flags line"]
        self.assertEqual(
            extract_rfc822_bytes(data),
            b"From: a@b.com\r\nSubject: hi\r\n\r\nbody, much longer than the flags line",
        )

    def test_extract_rfc822_bytes_empty(self):
        self.assertIsNone(extract_rfc822_bytes([]))
        self.assertIsNone(extract_rfc822_bytes([None]))


if __name__ == "__main__":
    unittest.main()
