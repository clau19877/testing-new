#!/usr/bin/env python3
import imaplib
import socket
import unittest

from fetch_icloud_code import explain_imap_failure, extract_code, looks_relevant, strip_html


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


if __name__ == "__main__":
    unittest.main()
