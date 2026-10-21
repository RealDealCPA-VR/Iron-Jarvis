import unittest

from pricing import price_with_tax
from quote import quote_total
from receipt import tax_part


class TaxTests(unittest.TestCase):
    def test_price(self):
        self.assertEqual(price_with_tax(100), 108.25)

    def test_quote(self):
        self.assertEqual(quote_total([40, 60]), {"net": 100, "tax": 8.25, "total": 108.25})

    def test_receipt(self):
        self.assertEqual(tax_part(108.25), 8.25)


if __name__ == "__main__":
    unittest.main()
