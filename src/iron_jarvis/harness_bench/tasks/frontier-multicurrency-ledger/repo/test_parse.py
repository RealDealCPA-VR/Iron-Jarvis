import datetime
import os
import tempfile
import unittest

from ledger.errors import LedgerError, ParseError
from ledger.money import Money
from ledger.parse import Transaction, parse_text, read_transactions

SAMPLE = """# my accounts
date,account,amount,description
2024-01-05,Assets:Bank,1000.00,opening balance

2024-01-06,Expenses:Food, -12.50 ,lunch
# a comment between rows
2024-01-07,Assets:Bank,-12.50,
"""


class ParseTests(unittest.TestCase):
    def test_reads_every_transaction_in_order(self):
        txns = parse_text(SAMPLE)
        self.assertEqual(len(txns), 3)
        self.assertEqual(
            txns[0],
            Transaction(datetime.date(2024, 1, 5), "Assets:Bank", Money("1000.00"), "opening balance"),
        )
        self.assertEqual(txns[1].amount, Money("-12.50"))
        self.assertEqual(txns[2].description, "")

    def test_header_names_ignore_case_and_order(self):
        txns = parse_text(" Amount ,DATE,Account\n5,2024-02-01,Cash\n")
        self.assertEqual(txns, [Transaction(datetime.date(2024, 2, 1), "Cash", Money("5"), "")])

    def test_errors_name_the_line(self):
        cases = [
            ("date,account,amount\n2024-02-30,Cash,1\n", 2, "bad date '2024-02-30'"),
            ("date,account,amount\n2024-02-01,,1\n", 2, "missing account"),
            ("# note\ndate,account,amount\n2024-02-01,Cash,1.234\n", 3, "bad amount '1.234'"),
            ("date,account,amount\n2024-02-01,Cash,1,extra\n", 2, "expected 3 cells, found 4"),
        ]
        for text, line, problem in cases:
            with self.subTest(problem=problem):
                with self.assertRaises(ParseError) as caught:
                    parse_text(text)
                self.assertEqual(caught.exception.line, line)
                self.assertEqual(str(caught.exception), f"line {line}: {problem}")
                self.assertIsInstance(caught.exception, LedgerError)

    def test_header_problems(self):
        cases = [
            ("date,account\n", "missing column 'amount'"),
            ("date,account,amount,memo\n", "unknown column 'memo'"),
            ("date,account,amount,Date\n", "duplicate column 'date'"),
        ]
        for text, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(ParseError) as caught:
                    parse_text(text)
                self.assertEqual(str(caught.exception), f"line 1: {message}")

    def test_a_file_with_no_header_holds_nothing(self):
        self.assertEqual(parse_text(""), [])
        self.assertEqual(parse_text("# only a comment\n\n"), [])

    def test_read_transactions_reads_a_file_with_a_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.csv")
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                fh.write("date,account,amount\r\n2024-03-01,Cash,2.00\r\n")
            self.assertEqual([t.amount for t in read_transactions(path)], [Money("2.00")])


if __name__ == "__main__":
    unittest.main()
