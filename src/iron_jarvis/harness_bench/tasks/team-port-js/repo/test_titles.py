import unittest

from strutil import title_case


class TitleCaseTest(unittest.TestCase):
    def test_small_words_stay_small(self):
        self.assertEqual(title_case("the old man and the sea"), "The Old Man and the Sea")

    def test_every_word(self):
        self.assertEqual(title_case("hello world"), "Hello World")


if __name__ == "__main__":
    unittest.main()
