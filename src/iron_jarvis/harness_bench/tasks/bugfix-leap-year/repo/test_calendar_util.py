import unittest

from calendar_util import days_in_month, days_in_year


class CalendarTests(unittest.TestCase):
    def test_leap_february(self):
        self.assertEqual(days_in_month(2024, 2), 29)

    def test_plain_february(self):
        self.assertEqual(days_in_month(2023, 2), 28)

    def test_short_month(self):
        self.assertEqual(days_in_month(2023, 4), 30)

    def test_year_length(self):
        self.assertEqual(days_in_year(2023), 365)

    def test_bad_month(self):
        with self.assertRaises(ValueError):
            days_in_month(2023, 13)


if __name__ == "__main__":
    unittest.main()
