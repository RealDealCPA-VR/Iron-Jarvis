import unittest

from durations import format_duration, parse_duration


class ParseDurationTest(unittest.TestCase):
    def test_parts(self):
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("2d 4h"), 187200)

    def test_a_bare_number_is_seconds(self):
        self.assertEqual(parse_duration("45"), 45)

    def test_unknown_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("3y")


class FormatDurationTest(unittest.TestCase):
    def test_two_parts(self):
        self.assertEqual(format_duration(5400), "1h 30m")

    def test_zero(self):
        self.assertEqual(format_duration(0), "0s")


if __name__ == "__main__":
    unittest.main()
