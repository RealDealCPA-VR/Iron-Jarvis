import unittest
from datetime import date

from scheduling.business_days import add_business_days, business_days_between, is_business_day


class BusinessDaysTest(unittest.TestCase):
    def test_weekends(self):
        self.assertTrue(is_business_day(date(2026, 10, 23)))  # a Friday
        self.assertFalse(is_business_day(date(2026, 10, 24)))  # a Saturday

    def test_add_over_a_weekend(self):
        self.assertEqual(add_business_days(date(2026, 10, 23), 1), date(2026, 10, 26))

    def test_add_over_a_holiday(self):
        self.assertEqual(add_business_days(date(2026, 12, 24), 1, holidays=[date(2026, 12, 25)]),
                         date(2026, 12, 28))

    def test_between(self):
        self.assertEqual(business_days_between(date(2026, 10, 19), date(2026, 10, 26)), 5)


if __name__ == "__main__":
    unittest.main()
