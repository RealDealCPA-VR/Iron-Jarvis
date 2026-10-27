import unittest

from shipping.boxes import choose_box
from shipping.weights import parcel_grams


class WeightsTest(unittest.TestCase):
    def test_whole_grams(self):
        self.assertEqual(parcel_grams([(2, 250, "g")]), 500)

    def test_rounded_up_once_for_the_parcel(self):
        self.assertEqual(parcel_grams([(1, "0.5", "oz"), (1, "0.5", "oz")]), 29)

    def test_mixed_units(self):
        self.assertEqual(parcel_grams([(1, "0.1", "lb"), (1, 3, "oz")]), 131)

    def test_an_empty_parcel(self):
        self.assertEqual(parcel_grams([]), 0)


class BoxesTest(unittest.TestCase):
    def test_the_smallest_box_that_fits(self):
        self.assertEqual(choose_box(parcel_grams([(4, "0.125", "kg")])), "letter")
        self.assertEqual(choose_box(2001), "medium")

    def test_too_heavy(self):
        with self.assertRaises(ValueError):
            choose_box(30001)


if __name__ == "__main__":
    unittest.main()
