import unittest

from ledger.book import Book
from ledger.parse import parse_text
from ledger.report import render

CSV = """date,account,amount
2024-01-05,Assets:Bank,1000.00
2024-01-06,Expenses:Food,-12.00
"""

EXPECTED = """Ledger report
=============

Assets:Bank    USD  1,000.00
Expenses:Food  USD    -12.00
----------------------------
Total          USD    988.00
"""


class ReportTests(unittest.TestCase):
    def test_report(self):
        self.assertEqual(render(Book.from_transactions(parse_text(CSV))), EXPECTED)

    def test_widths_follow_the_longest_label_and_amount(self):
        text = "date,account,amount\n2024-01-05,A,-1234567.00\n2024-01-06,Liabilities:Card:Visa,1.5\n"
        self.assertEqual(
            render(Book.from_transactions(parse_text(text))),
            "Ledger report\n"
            "=============\n"
            "\n"
            "A                      USD  -1,234,567.00\n"
            "Liabilities:Card:Visa  USD           1.50\n"
            "-----------------------------------------\n"
            "Total                  USD  -1,234,565.50\n",
        )

    def test_an_empty_book(self):
        self.assertEqual(render(Book()), "Ledger report\n=============\n\n(no transactions)\n")


if __name__ == "__main__":
    unittest.main()
