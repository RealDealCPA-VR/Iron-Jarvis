import unittest

from ledger.book import Book
from ledger.errors import UnknownAccount
from ledger.money import Money
from ledger.parse import parse_text

CSV = """date,account,amount,description
2024-01-05,Assets:Bank,1000.00,opening balance
2024-01-06,Expenses:Food,12.50,lunch
2024-01-06,Assets:Bank,-12.50,lunch
2024-01-09,Expenses:Rent,400,rent
2024-01-09,Assets:Bank,-400,rent
"""


class BookTests(unittest.TestCase):
    def setUp(self):
        self.book = Book.from_transactions(parse_text(CSV))

    def test_balances(self):
        self.assertEqual(self.book.balance("Assets:Bank"), Money("587.50"))
        self.assertEqual(self.book.balance("Expenses:Rent"), Money("400"))

    def test_accounts_are_sorted(self):
        self.assertEqual(self.book.accounts(), ["Assets:Bank", "Expenses:Food", "Expenses:Rent"])

    def test_total(self):
        self.assertEqual(self.book.total(), Money("1000.00"))

    def test_unknown_account(self):
        with self.assertRaises(UnknownAccount) as caught:
            self.book.balance("Nope")
        self.assertEqual(str(caught.exception), "no such account 'Nope'")


if __name__ == "__main__":
    unittest.main()
