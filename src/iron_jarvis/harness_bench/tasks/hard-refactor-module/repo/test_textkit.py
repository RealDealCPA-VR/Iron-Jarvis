import unittest

from textkit import histogram, sentences, top_words, words

TEXT = "The cat sat. The cat ran! Did the dog see the cat?"


class TextkitTests(unittest.TestCase):
    def test_words(self):
        self.assertEqual(words("Don't stop, Mr. Brown")[:2], ["don't", "stop"])

    def test_sentences(self):
        self.assertEqual(len(sentences(TEXT)), 3)

    def test_top_words(self):
        self.assertEqual(top_words(TEXT, 2), [("cat", 3), ("did", 1)])

    def test_histogram(self):
        self.assertEqual(histogram(TEXT, 1), "cat | ############################## 3")


if __name__ == "__main__":
    unittest.main()
