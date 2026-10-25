"""The first tests of multi-currency support (SPEC.md, section 7).

They fail until section 7 is written. They cover only the basics; SPEC.md
section 7 is the whole of what is asked.
"""

import os
import subprocess
import sys
import tempfile
import unittest

from ledger.book import Book
from ledger.money import Money
from ledger.parse import parse_text

HERE = os.path.dirname(os.path.abspath(__file__))

CSV = """date,account,amount,description,currency
2024-05-01,Assets:Bank,100.00,from savings,EUR
2024-05-02,Assets:Bank,500.00,salary,USD
2024-05-03,Expenses:Rent,-250.00,rent,EUR
2024-05-04,Expenses:Food,-20.50,groceries,USD
"""


class MultiCurrencyBasics(unittest.TestCase):
    def test_a_currency_column_gives_each_amount_its_currency(self):
        txns = parse_text(CSV)
        self.assertEqual([t.amount.currency for t in txns], ["EUR", "USD", "EUR", "USD"])
        self.assertEqual(txns[0].amount, Money("100.00", "EUR"))

    def test_without_a_currency_column_amounts_are_dollars(self):
        (txn,) = parse_text("date,account,amount\n2024-01-02,Assets:Bank,10.00\n")
        self.assertEqual(txn.amount.currency, "USD")
        self.assertEqual(txn.amount, Money("10.00", "USD"))

    def test_adding_two_currencies_is_refused(self):
        from ledger.errors import CurrencyMismatch, LedgerError

        self.assertTrue(issubclass(CurrencyMismatch, LedgerError))
        with self.assertRaises(CurrencyMismatch):
            Money("1.00", "EUR") + Money("1.00", "USD")

    def test_balances_are_kept_per_currency(self):
        book = Book.from_transactions(parse_text(CSV))
        self.assertEqual(
            book.balances("Assets:Bank"),
            {"EUR": Money("100.00", "EUR"), "USD": Money("500.00", "USD")},
        )
        self.assertEqual(book.totals(), {"EUR": Money("-150.00", "EUR"), "USD": Money("479.50", "USD")})

    def test_report_with_a_base_currency(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "transactions.csv")
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(CSV)
            with open(os.path.join(tmp, "rates.csv"), "w", encoding="utf-8", newline="") as fh:
                fh.write("base,currency,rate\nUSD,EUR,2\n")
            env = dict(os.environ)
            env.pop("PYTHONSAFEPATH", None)
            env["PYTHONPATH"] = HERE
            env["PYTHONIOENCODING"] = "utf-8"
            done = subprocess.run(
                [sys.executable, "-m", "ledger.cli", "report", path, "--base", "USD"],
                cwd=HERE, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
            )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout,
            "Ledger report\n"
            "=============\n"
            "\n"
            "Assets:Bank    EUR   100.00\n"
            "Assets:Bank    USD   500.00\n"
            "Expenses:Food  USD   -20.50\n"
            "Expenses:Rent  EUR  -250.00\n"
            "---------------------------\n"
            "Total          EUR  -150.00\n"
            "Total          USD   479.50\n"
            "---------------------------\n"
            "Grand total    USD   179.50\n",
        )


if __name__ == "__main__":
    unittest.main()
