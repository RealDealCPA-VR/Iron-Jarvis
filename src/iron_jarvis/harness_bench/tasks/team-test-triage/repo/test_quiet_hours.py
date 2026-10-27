import unittest
from datetime import datetime, time

from notify.quiet_hours import in_quiet_hours, next_send_time


class QuietHoursTest(unittest.TestCase):
    def test_late_evening_is_quiet(self):
        self.assertTrue(in_quiet_hours(time(23, 0)))

    def test_early_morning_is_quiet(self):
        self.assertTrue(in_quiet_hours(datetime(2026, 10, 27, 6, 30)))

    def test_noon_is_not(self):
        self.assertFalse(in_quiet_hours(time(12, 0)))

    def test_a_lunch_break(self):
        self.assertTrue(in_quiet_hours(time(13, 45), start=time(13, 0), end=time(14, 30)))

    def test_a_message_at_night_waits_for_the_morning(self):
        self.assertEqual(next_send_time(datetime(2026, 10, 27, 23, 30)), datetime(2026, 10, 28, 7, 0))

    def test_a_message_in_the_day_goes_now(self):
        moment = datetime(2026, 10, 27, 15, 5)
        self.assertEqual(next_send_time(moment), moment)


if __name__ == "__main__":
    unittest.main()
