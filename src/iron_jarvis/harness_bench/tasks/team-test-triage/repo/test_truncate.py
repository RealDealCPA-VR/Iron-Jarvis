import unittest

from textkit.truncate import truncate


class TruncateTest(unittest.TestCase):
    def test_short_text_is_kept(self):
        self.assertEqual(truncate("short", 10), "short")

    def test_cut_at_a_space(self):
        self.assertEqual(truncate("the quick brown fox", 14), "the quick...")

    def test_no_space_to_cut_at(self):
        self.assertEqual(truncate("abcdefghij", 6), "abc...")


if __name__ == "__main__":
    unittest.main()
