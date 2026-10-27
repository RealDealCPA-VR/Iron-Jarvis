import unittest

from stats.percentiles import median, percentile, summary


class PercentileTest(unittest.TestCase):
    def test_the_median_of_an_odd_count(self):
        self.assertEqual(median([5, 1, 3]), 3.0)

    def test_the_median_of_an_even_count(self):
        self.assertEqual(median([4, 1, 3, 2]), 2.5)

    def test_the_ends(self):
        self.assertEqual(percentile([3, 1, 2], 0), 1.0)
        self.assertEqual(percentile([3, 1, 2], 100), 3.0)

    def test_summary(self):
        self.assertEqual(summary([10, 30, 20]), {"min": 10.0, "median": 20.0, "max": 30.0})

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            percentile([], 50)
        with self.assertRaises(ValueError):
            percentile([1, 2], 101)


if __name__ == "__main__":
    unittest.main()
