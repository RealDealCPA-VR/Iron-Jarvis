import unittest

from orderdesk.customers import parse_customer
from orderdesk.invoice import render_invoice

ADA = {"id": "c-17", "name": "Ada Byron", "email": "ada@example.com"}
LINES = [{"name": "Notebook", "price_cents": 1250, "qty": 2}, {"name": "Fountain pen", "price_cents": 4500, "qty": 1}]


class InvoiceTest(unittest.TestCase):
    def test_without_coupon(self):
        text = render_invoice(parse_customer(ADA), LINES)
        self.assertEqual(text.splitlines(), [
            "Invoice for Ada Byron <ada@example.com>",
            "  2 x Notebook: $25.00",
            "  1 x Fountain pen: $45.00",
            "Subtotal: $70.00",
            "Shipping: $5.99",
            "Total: $75.99",
        ])

    def test_with_coupon(self):
        text = render_invoice(parse_customer(ADA), LINES, coupon="SAVE10")
        self.assertIn("Discount (10%): -$7.00", text)
        self.assertTrue(text.endswith("Total: $68.99"))

    def test_express(self):
        text = render_invoice(parse_customer(ADA), LINES, express=True)
        self.assertIn("Shipping (express): $12.99", text)
        self.assertTrue(text.endswith("Total: $82.99"))


if __name__ == "__main__":
    unittest.main()
