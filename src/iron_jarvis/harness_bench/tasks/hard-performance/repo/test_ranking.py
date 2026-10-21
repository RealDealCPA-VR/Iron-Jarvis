import unittest

from ranking import count_inversions, kendall_distance, sortedness


class RankingTests(unittest.TestCase):
    def test_inversions(self):
        self.assertEqual(count_inversions([3, 1, 2]), 2)
        self.assertEqual(count_inversions([1, 2, 3]), 0)

    def test_kendall(self):
        self.assertEqual(kendall_distance(["a", "b", "c"], ["c", "b", "a"]), 3)

    def test_sortedness(self):
        self.assertEqual(sortedness([1, 2, 3, 4]), 1.0)
        self.assertEqual(sortedness([4, 3, 2, 1]), 0.0)


if __name__ == "__main__":
    unittest.main()
