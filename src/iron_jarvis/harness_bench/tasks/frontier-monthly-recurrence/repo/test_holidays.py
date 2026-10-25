import unittest
from datetime import date

from recur import holidays_for, observed


class HolidayTests(unittest.TestCase):
    def test_weekday_holiday_is_observed_on_the_day(self):
        self.assertEqual(observed(date(2025, 12, 25)), date(2025, 12, 25))

    def test_saturday_moves_to_friday_and_sunday_to_monday(self):
        self.assertEqual(observed(date(2026, 7, 4)), date(2026, 7, 3))
        self.assertEqual(observed(date(2027, 7, 4)), date(2027, 7, 5))

    def test_holidays_for_2025(self):
        self.assertEqual(holidays_for(2025), {date(2025, 1, 1), date(2025, 7, 4), date(2025, 12, 25)})

    def test_new_years_day_on_a_saturday_belongs_to_the_year_before(self):
        # January 1, 2022 was a Saturday: observed on Friday, December 31, 2021.
        self.assertIn(date(2021, 12, 31), holidays_for(2021))
        self.assertNotIn(date(2021, 12, 31), holidays_for(2022))
        self.assertEqual(len(holidays_for(2022)), 2)


if __name__ == "__main__":
    unittest.main()
