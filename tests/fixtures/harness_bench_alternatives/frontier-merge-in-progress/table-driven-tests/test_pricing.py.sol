import unittest
from decimal import Decimal

from pricing import discounts, line_amount, order_total, shipping, subtotal


def money(text):
    return Decimal(text)


class Pricing(unittest.TestCase):
    """Both branches' tests, kept by name; their cases are written as tables."""

    def check_lines(self, cases):
        for price, qty, want in cases:
            with self.subTest(price=price, qty=qty):
                self.assertEqual(line_amount(price, qty), money(want))

    def test_line_amount(self):
        self.check_lines([("2.50", 4, "10.00")])

    def test_subtotal(self):
        self.assertEqual(subtotal([("2.50", 4), ("1.00", 3)]), money("13.00"))

    def test_member_subtotal(self):
        self.assertEqual(subtotal([("10.00", 4)], member=True), money("38.00"))

    def test_order_total_adds_tax(self):
        self.assertEqual(order_total([("10.00", 6)]), money("64.80"))

    def test_line_amount_is_rounded_half_even(self):
        self.check_lines([("0.125", 1, "0.12"), ("0.135", 1, "0.14"), ("0.3333", 3, "1.00")])

    def test_shipping(self):
        for amount, fee in (("49.99", "4.95"), ("50.00", "0.00")):
            with self.subTest(amount=amount):
                self.assertEqual(shipping(money(amount)), money(fee))

    def test_small_order_pays_shipping(self):
        self.assertEqual(order_total([("10.00", 2)]), money("26.55"))

    def test_bulk_discount(self):
        self.check_lines([("2.00", 10, "18.00"), ("2.00", 9, "18.00"), ("3.00", 20, "54.00")])

    def test_member_discount_needs_a_minimum(self):
        for lines, want in (([("5.00", 3)], "15.00"), ([("10.00", 2)], "19.00")):
            with self.subTest(lines=lines):
                self.assertEqual(subtotal(lines, member=True), money(want))

    def test_discounts_in_words(self):
        table = [
            ([("2.00", 10), ("1.00", 1)], ["bulk: line 1"]),
            ([("1.00", 1), ("1.00", 12), ("10.00", 2)], ["bulk: line 2", "member"]),
        ]
        for lines, want in table:
            with self.subTest(lines=lines):
                self.assertEqual(discounts(lines, member=True), want)


if __name__ == "__main__":
    unittest.main()
