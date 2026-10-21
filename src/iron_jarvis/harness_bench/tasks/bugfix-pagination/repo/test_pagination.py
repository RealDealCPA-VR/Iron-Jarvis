import unittest

from pagination import page, page_count


class PageTests(unittest.TestCase):
    def test_first_page(self):
        self.assertEqual(page(list(range(25)), 1), list(range(10)))

    def test_last_page_is_partial(self):
        self.assertEqual(page(list(range(25)), 3), [20, 21, 22, 23, 24])

    def test_page_count_rounds_up(self):
        self.assertEqual(page_count(list(range(25))), 3)


if __name__ == "__main__":
    unittest.main()
