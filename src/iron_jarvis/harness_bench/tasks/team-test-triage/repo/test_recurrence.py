import unittest
from datetime import date

from scheduling.recurrence import describe, nth_weekday

MONDAY, TUESDAY, THURSDAY, FRIDAY = 0, 1, 3, 4


class NthWeekdayTest(unittest.TestCase):
    def test_the_second_tuesday(self):
        self.assertEqual(nth_weekday(2026, 11, TUESDAY, 2), date(2026, 11, 10))

    def test_thanksgiving(self):
        self.assertEqual(nth_weekday(2026, 11, THURSDAY, 4), date(2026, 11, 26))

    def test_the_last_monday_of_may(self):
        self.assertEqual(nth_weekday(2026, 5, MONDAY, -1), date(2026, 5, 25))

    def test_the_last_friday_of_october(self):
        self.assertEqual(nth_weekday(2026, 10, FRIDAY, -1), date(2026, 10, 30))

    def test_no_fifth_monday_in_february(self):
        with self.assertRaises(ValueError):
            nth_weekday(2026, 2, MONDAY, 5)

    def test_n_zero(self):
        with self.assertRaises(ValueError):
            nth_weekday(2026, 2, MONDAY, 0)


class DescribeTest(unittest.TestCase):
    def test_words(self):
        self.assertEqual(describe(TUESDAY, 2), "the second Tuesday")
        self.assertEqual(describe(FRIDAY, -1), "the last Friday")


if __name__ == "__main__":
    unittest.main()
