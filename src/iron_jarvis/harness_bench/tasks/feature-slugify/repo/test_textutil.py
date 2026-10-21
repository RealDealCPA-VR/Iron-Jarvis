import unittest

from textutil import collapse_spaces, title_case


class TextUtilTests(unittest.TestCase):
    def test_title_case(self):
        self.assertEqual(title_case("hello   big world"), "Hello Big World")

    def test_collapse_spaces(self):
        self.assertEqual(collapse_spaces("  a \t b\n c  "), "a b c")


if __name__ == "__main__":
    unittest.main()
