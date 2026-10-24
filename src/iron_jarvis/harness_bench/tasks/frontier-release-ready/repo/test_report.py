import os
import tempfile
import unittest

from invoicing.report import summarize

HERE = os.path.dirname(os.path.abspath(__file__))


def export(text):
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


class Summarize(unittest.TestCase):
    def test_the_sample_export(self):
        self.assertEqual(
            summarize(os.path.join(HERE, "data", "sample.csv")),
            {"Paint": "61.48", "Hardware": "12.50", "Garden": "100.00"},
        )

    def test_a_category_with_a_comma(self):
        path = export('customer,category,amount\nAda,"Tools, hand",10.00\nBob,"Tools, hand",5.50\n')
        try:
            self.assertEqual(summarize(path), {"Tools, hand": "15.50"})
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
