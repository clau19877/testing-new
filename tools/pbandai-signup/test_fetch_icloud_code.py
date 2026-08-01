#!/usr/bin/env python3
import unittest

from fetch_icloud_code import extract_code, looks_relevant, strip_html


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


if __name__ == "__main__":
    unittest.main()
