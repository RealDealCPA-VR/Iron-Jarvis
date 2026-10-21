import io
import os
import tempfile
import unittest

import wc


class LineCountTests(unittest.TestCase):
    def test_counts_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "a.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("one\ntwo\nthree\n")
            out = io.StringIO()
            self.assertEqual(wc.main([path], out=out), 0)
            self.assertEqual(out.getvalue(), f"3 {path}\n")


if __name__ == "__main__":
    unittest.main()
