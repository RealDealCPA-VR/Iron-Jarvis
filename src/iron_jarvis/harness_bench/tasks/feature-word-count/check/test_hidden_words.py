# IJ-BENCH-HIDDEN-CHECK feature-word-count
import io
import os
import tempfile
import unittest

import wc


def _write(folder, name, text):
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class HiddenWords(unittest.TestCase):
    def test_count_words(self):
        self.assertEqual(wc.count_words("one two\n  three "), 3)
        self.assertEqual(wc.count_words(""), 0)
        self.assertEqual(wc.count_words("\n\t \n"), 0)
        self.assertEqual(wc.count_words("a-b c.d"), 2)

    def test_long_option(self):
        with tempfile.TemporaryDirectory() as folder:
            path = _write(folder, "a.txt", "the quick brown\nfox\n")
            out = io.StringIO()
            self.assertEqual(wc.main(["--words", path], out=out), 0)
            self.assertEqual(out.getvalue(), f"4 {path}\n")

    def test_short_option_with_total(self):
        with tempfile.TemporaryDirectory() as folder:
            one = _write(folder, "a.txt", "a b c\n")
            two = _write(folder, "b.txt", "d e\nf g h\n")
            out = io.StringIO()
            self.assertEqual(wc.main(["-w", one, two], out=out), 0)
            self.assertEqual(out.getvalue(), f"3 {one}\n5 {two}\n8 total\n")

    def test_lines_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            one = _write(folder, "a.txt", "a b c\n")
            two = _write(folder, "b.txt", "d e\nf g h\n")
            out = io.StringIO()
            self.assertEqual(wc.main([one, two], out=out), 0)
            self.assertEqual(out.getvalue(), f"1 {one}\n2 {two}\n3 total\n")
