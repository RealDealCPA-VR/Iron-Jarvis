import unittest

from stats_report import summary
from utils import initials, mean, median, shout


class UtilsTests(unittest.TestCase):
    def test_strings(self):
        self.assertEqual(shout("hi"), "HI!")
        self.assertEqual(initials("ada  lovelace"), "AL")

    def test_math(self):
        self.assertEqual(mean([1, 2, 3, 4]), 2.5)
        self.assertEqual(median([5, 1, 3]), 3)
        self.assertEqual(median([4, 1, 3, 2]), 2.5)

    def test_summary(self):
        self.assertEqual(summary("Ada Lovelace", [70, 80, 95]), "AL: mean 81.7, median 80.0")


if __name__ == "__main__":
    unittest.main()
