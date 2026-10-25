import unittest
from decimal import Decimal

from pricing import discounts, line_amount, order_total, shipping, subtotal


class Pricing(unittest.TestCase):
    def test_line_amount(self):
        self.assertEqual(line_amount("2.50", 4), Decimal("10.00"))

    def test_subtotal(self):
        self.assertEqual(subtotal([("2.50", 4), ("1.00", 3)]), Decimal("13.00"))

    def test_member_subtotal(self):
        self.assertEqual(subtotal([("10.00", 4)], member=True), Decimal("38.00"))

    def test_order_total_adds_tax(self):
        self.assertEqual(order_total([("10.00", 6)]), Decimal("64.80"))

    def test_line_amount_is_rounded_half_even(self):
        self.assertEqual(line_amount("0.125", 1), Decimal("0.12"))
        self.assertEqual(line_amount("0.135", 1), Decimal("0.14"))
        self.assertEqual(line_amount("0.3333", 3), Decimal("1.00"))

    def test_shipping(self):
        self.assertEqual(shipping(Decimal("49.99")), Decimal("4.95"))
        self.assertEqual(shipping(Decimal("50.00")), Decimal("0.00"))

    def test_small_order_pays_shipping(self):
        self.assertEqual(order_total([("10.00", 2)]), Decimal("26.55"))

    def test_bulk_discount(self):
        self.assertEqual(line_amount("2.00", 10), Decimal("18.00"))
        self.assertEqual(line_amount("2.00", 9), Decimal("18.00"))
        self.assertEqual(line_amount("3.00", 20), Decimal("54.00"))

    def test_member_discount_needs_a_minimum(self):
        self.assertEqual(subtotal([("5.00", 3)], member=True), Decimal("15.00"))
        self.assertEqual(subtotal([("10.00", 2)], member=True), Decimal("19.00"))

    def test_discounts_in_words(self):
        self.assertEqual(discounts([("2.00", 10), ("1.00", 1)], member=True), ["bulk: line 1"])
        self.assertEqual(discounts([("1.00", 1), ("1.00", 12), ("10.00", 2)], member=True),
                         ["bulk: line 2", "member"])


if __name__ == "__main__":
    unittest.main()
