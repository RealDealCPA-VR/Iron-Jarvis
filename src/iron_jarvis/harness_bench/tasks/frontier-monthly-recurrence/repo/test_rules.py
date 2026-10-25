import unittest
from datetime import date, datetime

from recur import last_business_days, monthly, weekly


class MonthlyTests(unittest.TestCase):
    def test_keeps_the_day_of_month(self):
        self.assertEqual(
            monthly(date(2025, 1, 15), 3),
            [date(2025, 1, 15), date(2025, 2, 15), date(2025, 3, 15)],
        )

    def test_crosses_the_year(self):
        self.assertEqual(
            monthly(date(2024, 11, 5), 3),
            [date(2024, 11, 5), date(2024, 12, 5), date(2025, 1, 5)],
        )

    def test_interval(self):
        self.assertEqual(
            monthly(date(2025, 1, 10), 3, interval=3),
            [date(2025, 1, 10), date(2025, 4, 10), date(2025, 7, 10)],
        )

    def test_count(self):
        self.assertEqual(monthly(date(2025, 6, 1), 0), [])
        self.assertEqual(monthly(date(2025, 6, 1), 1), [date(2025, 6, 1)])
        with self.assertRaises(ValueError):
            monthly(date(2025, 6, 1), -1)

    def test_a_datetime_is_refused(self):
        with self.assertRaises(TypeError):
            monthly(datetime(2025, 1, 31, 9, 0), 2)

    def test_bug_report_january_31_does_not_skip_february(self):
        # Reported: a monthly event starting on January 31 jumps to March 3.
        self.assertEqual(monthly(date(2025, 1, 31), 2), [date(2025, 1, 31), date(2025, 2, 28)])
        self.assertEqual(monthly(date(2024, 1, 31), 2), [date(2024, 1, 31), date(2024, 2, 29)])


class WeeklyTests(unittest.TestCase):
    def test_weekly(self):
        self.assertEqual(
            weekly(date(2025, 12, 22), 3),
            [date(2025, 12, 22), date(2025, 12, 29), date(2026, 1, 5)],
        )

    def test_every_other_week(self):
        self.assertEqual(weekly(date(2025, 3, 3), 2, interval=2), [date(2025, 3, 3), date(2025, 3, 17)])


class LastBusinessDayTests(unittest.TestCase):
    def test_twelve_dates_starting_in_january(self):
        days = last_business_days(2025)
        self.assertEqual(len(days), 12)
        self.assertEqual(days[0], date(2025, 1, 31))

    def test_a_holiday_on_january_31_moves_it_back(self):
        self.assertEqual(last_business_days(2025, holidays={date(2025, 1, 31)})[0], date(2025, 1, 30))


if __name__ == "__main__":
    unittest.main()
