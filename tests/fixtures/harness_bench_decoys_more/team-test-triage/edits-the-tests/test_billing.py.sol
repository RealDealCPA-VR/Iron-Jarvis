import unittest
from datetime import date

from scheduling.billing import billing_dates, next_billing_date


class BillingTest(unittest.TestCase):
    def test_a_mid_month_anchor(self):
        self.assertEqual(billing_dates(date(2026, 1, 15), 3),
                         [date(2026, 1, 15), date(2026, 2, 15), date(2026, 3, 15)])

    def test_an_end_of_month_anchor(self):
        self.assertEqual(billing_dates(date(2026, 1, 31), 5), [
            date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 28), date(2026, 4, 28), date(2026, 5, 28)])

    def test_a_leap_year(self):
        self.assertEqual(billing_dates(date(2028, 1, 31), 3),
                         [date(2028, 1, 31), date(2028, 2, 29), date(2028, 3, 29)])

    def test_after_a_short_month(self):
        self.assertEqual(billing_dates(date(2026, 3, 31), 3),
                         [date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 30)])

    def test_next_billing_date(self):
        self.assertEqual(next_billing_date(date(2026, 1, 31), date(2026, 2, 28)), date(2026, 3, 28))

    def test_next_billing_date_mid_month(self):
        self.assertEqual(next_billing_date(date(2026, 1, 15), date(2026, 3, 1)), date(2026, 3, 15))


if __name__ == "__main__":
    unittest.main()
