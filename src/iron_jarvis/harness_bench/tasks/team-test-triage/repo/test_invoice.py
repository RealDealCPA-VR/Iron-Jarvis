import unittest

from money.invoice import invoice_totals


class InvoiceTotalsTest(unittest.TestCase):
    def test_simple_invoice(self):
        self.assertEqual(invoice_totals([("book", 1000)], 10), {"subtotal": 1000, "tax": 100, "total": 1100})

    def test_the_tax_is_per_line(self):
        totals = invoice_totals([("pen", 250), ("pad", 1050)], 5)
        self.assertEqual(totals, {"subtotal": 1300, "tax": 66, "total": 1366})


if __name__ == "__main__":
    unittest.main()
