import unittest

from durations import parse_duration


class DurationTests(unittest.TestCase):
    def test_minutes(self):
        self.assertEqual(parse_duration("45m"), 2700)

    def test_hours_and_minutes(self):
        self.assertEqual(parse_duration("1h30m"), 5400)

    def test_seconds_and_hours(self):
        self.assertEqual(parse_duration("90s"), 90)
        self.assertEqual(parse_duration("2h"), 7200)

    def test_rejects_junk(self):
        with self.assertRaises(ValueError):
            parse_duration("soon")


if __name__ == "__main__":
    unittest.main()
