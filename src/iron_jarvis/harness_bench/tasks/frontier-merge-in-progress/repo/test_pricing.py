import unittest
from decimal import Decimal

from pricing import line_amount, order_total, subtotal


class Pricing(unittest.TestCase):
    def test_line_amount(self):
        self.assertEqual(line_amount("2.50", 4), Decimal("10.00"))

    def test_subtotal(self):
        self.assertEqual(subtotal([("2.50", 4), ("1.00", 3)]), Decimal("13.00"))

    def test_member_subtotal(self):
        self.assertEqual(subtotal([("10.00", 4)], member=True), Decimal("38.00"))

    def test_order_total_adds_tax(self):
        self.assertEqual(order_total([("10.00", 6)]), Decimal("64.80"))


if __name__ == "__main__":
    unittest.main()
