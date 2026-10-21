"""The phone-number rules, one test per case.

A US number is ten digits: a three-digit area code and a three-digit exchange
(neither may start with 0 or 1), then four digits. A leading country code 1 is
allowed, an extension ("ext 12", "x12") is dropped, and only the ASCII digits
0-9 count as digits.
"""
import unittest

from phone import normalize_phone

CASES = [
    ("555-212-4567", "+15552124567"),
    ("(555) 212-4567", "+15552124567"),
    ("555.212.4567", "+15552124567"),
    ("1 555 212 4567", "+15552124567"),
    ("+1 (555) 212-4567", "+15552124567"),
    ("5552124567", "+15552124567"),
    ("15552124567", "+15552124567"),
    ("555-1234", None),
    ("", None),
    ("not a number", None),
    ("555-212-4567 ext 89", "+15552124567"),
    ("555-212-4567 x12", "+15552124567"),
    ("055-212-4567", None),
    ("155-212-4567", None),
    ("555-012-4567", None),
    ("555-112-4567", None),
    ("555-212-45678", None),
    ("2125551234", "+12125551234"),
    ("+44 20 7946 0958", None),
    ("1-800-FLOWERS", None),
    ("５５５２１２４５６７", None),
    ("  555 212 4567  ", "+15552124567"),
    ("555-212-4567\n", "+15552124567"),
    ("1-555-212-4567", "+15552124567"),
    ("2-555-212-4567", None),
    ("(212)555-1234", "+12125551234"),
    ("212 555 1234 ext. 5", "+12125551234"),
    ("911", None),
    ("555 212 456", None),
    ("+1 212 555 1234", "+12125551234"),
]


class PhoneTests(unittest.TestCase):
    pass


def _make(raw, want):
    def test(self):
        self.assertEqual(normalize_phone(raw), want)

    return test


for _index, (_raw, _want) in enumerate(CASES):
    setattr(PhoneTests, f"test_case_{_index:02d}", _make(_raw, _want))


if __name__ == "__main__":
    unittest.main()
