import unittest

from strutil import wrap_text


class WrapTextTest(unittest.TestCase):
    def test_words_fill_lines(self):
        self.assertEqual(wrap_text("one two three four", 9), "one two\nthree\nfour")

    def test_short_text_is_unchanged(self):
        self.assertEqual(wrap_text("short", 10), "short")

    def test_paragraphs_stay_apart(self):
        self.assertEqual(wrap_text("first\nsecond", 20), "first\nsecond")

    def test_width_must_be_positive(self):
        with self.assertRaises(ValueError):
            wrap_text("x", 0)


if __name__ == "__main__":
    unittest.main()
