import unittest

from strutil import natural_compare, natural_sort


class NaturalTest(unittest.TestCase):
    def test_numbers_sort_by_value(self):
        self.assertEqual(natural_sort(["item10", "item2", "item1"]), ["item1", "item2", "item10"])

    def test_compare(self):
        self.assertEqual(natural_compare("apple", "banana"), -1)
        self.assertEqual(natural_compare("banana", "apple"), 1)
        self.assertEqual(natural_compare("kiwi", "kiwi"), 0)

    def test_the_argument_is_left_alone(self):
        items = ["b2", "b1"]
        natural_sort(items)
        self.assertEqual(items, ["b2", "b1"])


if __name__ == "__main__":
    unittest.main()
