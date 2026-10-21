# IJ-BENCH-HIDDEN-CHECK bugfix-pagination
import unittest

from pagination import page, page_count


class HiddenPagination(unittest.TestCase):
    def test_pages_cover_every_item_once(self):
        items = list(range(23))
        got = []
        for number in range(1, page_count(items, 5) + 1):
            got.extend(page(items, number, 5))
        self.assertEqual(got, items)

    def test_exact_multiple(self):
        self.assertEqual(page_count(list(range(20)), 10), 2)
        self.assertEqual(page(list(range(20)), 2), list(range(10, 20)))

    def test_empty(self):
        self.assertEqual(page_count([], 10), 0)
        self.assertEqual(page([], 1), [])

    def test_past_the_end(self):
        self.assertEqual(page(list(range(5)), 2, 5), [])

    def test_size_one(self):
        self.assertEqual(page(["a", "b", "c"], 2, 1), ["b"])
        self.assertEqual(page_count(["a", "b", "c"], 1), 3)

    def test_bad_number(self):
        with self.assertRaises(ValueError):
            page([1], 0)
