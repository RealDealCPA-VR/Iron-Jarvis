import unittest

from textkit.slug import slugify


class SlugTest(unittest.TestCase):
    def test_plain_words(self):
        self.assertEqual(slugify("Hello, World!"), "hello-world")

    def test_accents(self):
        self.assertEqual(slugify("Café Crème"), "cafe-creme")

    def test_more_accents(self):
        self.assertEqual(slugify("naïve résumé"), "naive-resume")

    def test_a_ring(self):
        self.assertEqual(slugify("Ångström units"), "angstrom-units")

    def test_a_long_title_is_cut_between_words(self):
        self.assertEqual(slugify("one two three four", max_length=9), "one-two")


if __name__ == "__main__":
    unittest.main()
