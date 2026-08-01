#!/usr/bin/env python3
import unittest

from grizzly_sms import format_phone, phone_dropdown_iso


class GrizzlySmsTests(unittest.TestCase):
    def test_format_national_us(self):
        self.assertEqual(format_phone("+15551234567", "national", "1"), "5551234567")
        self.assertEqual(format_phone("15551234567", "national", "1"), "5551234567")
        self.assertEqual(format_phone("5551234567", "national", "1"), "5551234567")

    def test_format_e164(self):
        self.assertEqual(format_phone("5551234567", "e164", "1"), "+15551234567")
        self.assertEqual(format_phone("+1 (555) 123-4567", "e164", "1"), "+15551234567")

    def test_format_digits(self):
        self.assertEqual(format_phone("+1-555-123-4567", "digits", "1"), "15551234567")

    def test_phone_dropdown_iso_usa(self):
        self.assertEqual(phone_dropdown_iso(12), "US")
        self.assertEqual(phone_dropdown_iso("12"), "US")

    def test_phone_dropdown_iso_uk_uses_isle_of_man(self):
        self.assertEqual(phone_dropdown_iso(16), "IM")

    def test_phone_dropdown_iso_unknown_falls_back_to_us(self):
        self.assertEqual(phone_dropdown_iso(9999), "US")
        self.assertEqual(phone_dropdown_iso("not-a-number"), "US")

    def test_phone_dropdown_iso_config_override_wins(self):
        self.assertEqual(phone_dropdown_iso(16, {"phone_dropdown_iso": "GB"}), "GB")


if __name__ == "__main__":
    unittest.main()
