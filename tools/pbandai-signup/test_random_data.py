#!/usr/bin/env python3
import unittest

import random_data


class RandomDataTests(unittest.TestCase):
    def test_random_password_is_compliant(self):
        for _ in range(50):
            pw = random_data.random_password()
            self.assertTrue(random_data.is_compliant_password(pw), pw)

    def test_password_rules_reject_sequential_digits(self):
        self.assertFalse(random_data.is_compliant_password("Password234"))

    def test_password_rules_reject_repeated_char(self):
        self.assertFalse(random_data.is_compliant_password("Passsword1!"))

    def test_password_rules_reject_sequential_letters(self):
        self.assertFalse(random_data.is_compliant_password("Pabcword1!"))

    def test_password_rules_require_length(self):
        self.assertFalse(random_data.is_compliant_password("Ab1!"))
        self.assertFalse(random_data.is_compliant_password("A" * 25))

    def test_random_day_respects_month_bounds(self):
        for _ in range(50):
            day = int(random_data.random_day("2"))
            self.assertLessEqual(day, 28)
        for _ in range(50):
            day = int(random_data.random_day("1"))
            self.assertLessEqual(day, 31)

    def test_random_year_is_adult(self):
        from datetime import date

        for _ in range(50):
            year = int(random_data.random_year())
            self.assertLessEqual(year, date.today().year - 18)

    def test_random_gender_is_valid_value(self):
        for _ in range(20):
            self.assertIn(random_data.random_gender(), random_data.GENDERS)

    def test_random_city_state_zip_consistent_tuple(self):
        city, state, zip_code = random_data.random_city_state_zip()
        self.assertIn((city, state, zip_code), random_data.CITIES)


if __name__ == "__main__":
    unittest.main()
