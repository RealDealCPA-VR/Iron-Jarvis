# IJ-BENCH-HIDDEN-CHECK bugfix-leap-year
import unittest

from calendar_util import days_in_month, days_in_year, is_leap


class HiddenCalendar(unittest.TestCase):
    def test_century_rule(self):
        self.assertFalse(is_leap(1900))
        self.assertFalse(is_leap(2100))
        self.assertTrue(is_leap(2000))
        self.assertTrue(is_leap(2400))

    def test_ordinary_years(self):
        self.assertTrue(is_leap(2024))
        self.assertFalse(is_leap(2023))
        self.assertTrue(is_leap(1996))

    def test_month_and_year_follow(self):
        self.assertEqual(days_in_month(1900, 2), 28)
        self.assertEqual(days_in_month(2000, 2), 29)
        self.assertEqual(days_in_year(1900), 365)
        self.assertEqual(days_in_year(2000), 366)
