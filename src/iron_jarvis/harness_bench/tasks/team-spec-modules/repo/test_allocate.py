import unittest

from allocate import allocate, format_cents, split_evenly


class AllocateTest(unittest.TestCase):
    def test_even_split(self):
        self.assertEqual(split_evenly(100, 3), [34, 33, 33])

    def test_by_weight(self):
        self.assertEqual(allocate(1000, [1, 3]), [250, 750])

    def test_parts_add_up(self):
        self.assertEqual(sum(allocate(1001, [2, 3, 5])), 1001)

    def test_format(self):
        self.assertEqual(format_cents(123456), "$1,234.56")


if __name__ == "__main__":
    unittest.main()
