#!/usr/bin/env python3
"""Curated "database" of realistic values used to resolve task.csv "random" cells."""

from __future__ import annotations

import random
import string
from datetime import date

FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Avery", "Quinn",
    "Sam", "Drew", "Jamie", "Reese", "Skylar", "Peyton", "Emerson", "Rowan",
    "Hayden", "Parker", "Dakota", "Cameron", "Elliot", "Finley", "Harper",
    "Kendall", "Logan", "Marley", "Sawyer", "Sydney", "Tatum", "Blake",
    "Charlie", "Devin", "Ellis", "Gray", "Jules", "Kai", "Lane", "Micah",
    "Nova", "Oakley", "Remy", "Shay", "Wren", "August", "Bex", "Carmen",
]

LAST_NAMES = [
    "Miller", "Johnson", "Garcia", "Smith", "Williams", "Brown", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson",
    "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee",
    "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark", "Ramirez",
    "Lewis", "Robinson", "Walker", "Young", "Allen", "King", "Wright",
    "Scott", "Torres", "Nguyen", "Hill", "Flores", "Green", "Adams",
    "Nelson", "Baker", "Hall", "Rivera", "Campbell", "Mitchell", "Carter",
]

# (city, state, 5-digit zip) — kept consistent as a set when multiple of
# city/state/zip are all requested as "random" in the same row.
CITIES = [
    ("Los Angeles", "CA", "90001"),
    ("San Diego", "CA", "92101"),
    ("Sacramento", "CA", "95814"),
    ("San Jose", "CA", "95101"),
    ("Phoenix", "AZ", "85001"),
    ("Tucson", "AZ", "85701"),
    ("Las Vegas", "NV", "89101"),
    ("Reno", "NV", "89501"),
    ("Portland", "OR", "97201"),
    ("Seattle", "WA", "98101"),
    ("Denver", "CO", "80201"),
    ("Austin", "TX", "73301"),
    ("Dallas", "TX", "75201"),
    ("Houston", "TX", "77001"),
    ("Chicago", "IL", "60601"),
    ("Columbus", "OH", "43004"),
    ("Atlanta", "GA", "30301"),
    ("Orlando", "FL", "32801"),
    ("Miami", "FL", "33101"),
    ("New York", "NY", "10001"),
    ("Boston", "MA", "02101"),
    ("Philadelphia", "PA", "19019"),
]

STREET_NAMES = [
    "Main", "Oak", "Maple", "Cedar", "Pine", "Elm", "Washington", "Lake",
    "Hill", "Park", "Sunset", "Highland", "River", "Meadow", "Chestnut",
    "Willow", "Birch", "Spruce", "Franklin", "Jefferson",
]

STREET_SUFFIXES = ["St", "Ave", "Rd", "Dr", "Ln", "Blvd", "Ct", "Way"]

GENDERS = ["Male", "Female", "NotApplicable", "NotSelected"]

DAYS_IN_MONTH = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}

ALLOWED_PW_SYMBOLS = "`~!@#$%^&*()_-.'"
PW_WORD_POOL = [
    "Tiger", "River", "Cloud", "Comet", "Falcon", "Maple", "Ember", "Delta",
    "Nova", "Pixel", "Cobalt", "Willow", "Harbor", "Quartz", "Lumen",
    "Onyx", "Raven", "Cedar", "Aurora", "Basil", "Zephyr", "Ridge",
]


def random_first_name() -> str:
    return random.choice(FIRST_NAMES)


def random_last_name() -> str:
    return random.choice(LAST_NAMES)


def random_gender() -> str:
    return random.choice(GENDERS)


def random_month() -> str:
    return str(random.randint(1, 12))


def random_day(month: str | int | None = None) -> str:
    try:
        m = int(month) if month is not None and str(month).strip() else None
    except ValueError:
        m = None
    max_day = DAYS_IN_MONTH.get(m, 28) if m else 28
    return str(random.randint(1, max_day))


def random_year(min_age: int = 18, max_age: int = 45) -> str:
    current_year = date.today().year
    return str(random.randint(current_year - max_age, current_year - min_age))


def random_city_state_zip() -> tuple[str, str, str]:
    return random.choice(CITIES)


def random_street_address() -> str:
    return f"{random.randint(100, 9999)} {random.choice(STREET_NAMES)} {random.choice(STREET_SUFFIXES)}"


def random_country() -> str:
    # The real "Area" dropdown only offers Canada/US; weight heavily toward US.
    return random.choices(["United States", "Canada"], weights=[9, 1])[0]


def random_token(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def is_compliant_password(pw: str) -> bool:
    """Mirrors the rules shown on the real ENTER INFORMATION screen."""
    if not (8 <= len(pw) <= 20):
        return False
    allowed = set(ALLOWED_PW_SYMBOLS)
    for ch in pw:
        if not (ch.isalnum() or ch in allowed):
            return False
    for i in range(len(pw) - 2):
        if pw[i] == pw[i + 1] == pw[i + 2]:
            return False
    for i in range(len(pw) - 2):
        a, b, c = pw[i], pw[i + 1], pw[i + 2]
        if a.isdigit() and b.isdigit() and c.isdigit():
            va, vb, vc = int(a), int(b), int(c)
            if vb - va == 1 and vc - vb == 1:
                return False
            if va - vb == 1 and vb - vc == 1:
                return False
        elif a.isalpha() and b.isalpha() and c.isalpha():
            va, vb, vc = ord(a.lower()), ord(b.lower()), ord(c.lower())
            if vb - va == 1 and vc - vb == 1:
                return False
            if va - vb == 1 and vb - vc == 1:
                return False
    has_upper = any(ch.isupper() for ch in pw)
    has_lower = any(ch.islower() for ch in pw)
    has_digit = any(ch.isdigit() for ch in pw)
    has_symbol = any(ch in allowed for ch in pw)
    return sum([has_upper, has_lower, has_digit, has_symbol]) >= 3


def random_password() -> str:
    for _ in range(50):
        word1 = random.choice(PW_WORD_POOL)
        word2 = random.choice(PW_WORD_POOL).lower()
        digits = "".join(random.choice(string.digits) for _ in range(random.choice((2, 3))))
        symbol = random.choice(ALLOWED_PW_SYMBOLS)
        pw = f"{word1}{digits}{symbol}{word2}"[:20]
        if is_compliant_password(pw):
            return pw
    raise RuntimeError("failed to generate a compliant random password")
