import unittest

from report import to_csv


class ReportTests(unittest.TestCase):
    def test_plain_rows(self):
        text = to_csv([
            {"name": "Ann", "city": "Oslo", "amount": 3},
            {"name": "Bo", "city": "Lund", "amount": 12.5},
        ])
        self.assertEqual(text, "name,city,amount\nAnn,Oslo,3\nBo,Lund,12.5\n")

    def test_no_rows(self):
        self.assertEqual(to_csv([]), "name,city,amount\n")


if __name__ == "__main__":
    unittest.main()
