# IJ-BENCH-HIDDEN-CHECK bugfix-csv-quoting
import csv
import io
import unittest

from report import to_csv

ROWS = [
    {"name": "Smith, Jane", "city": "Paris", "amount": 10},
    {"name": 'O"Neil', "city": "Cork", "amount": 2.5},
    {"name": "Line\nBreak", "city": "Rome, IT", "amount": 0},
    {"name": "Plain", "city": "Bonn", "amount": 7},
]


class HiddenReport(unittest.TestCase):
    def test_round_trip(self):
        text = to_csv(ROWS)
        back = list(csv.reader(io.StringIO(text, newline="")))
        self.assertEqual(back[0], ["name", "city", "amount"])
        want = [[str(r["name"]), str(r["city"]), str(r["amount"])] for r in ROWS]
        self.assertEqual(back[1:], want)

    def test_line_endings(self):
        text = to_csv(ROWS)
        self.assertNotIn("\r\n", text)
        self.assertTrue(text.startswith("name,city,amount\n"))
        self.assertTrue(text.endswith("\n"))

    def test_plain_rows_unchanged(self):
        self.assertEqual(
            to_csv([{"name": "Ann", "city": "Oslo", "amount": 3}]),
            "name,city,amount\nAnn,Oslo,3\n",
        )
