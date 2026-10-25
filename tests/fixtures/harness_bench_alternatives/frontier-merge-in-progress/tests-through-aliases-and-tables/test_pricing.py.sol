import unittest
from decimal import Decimal as D

import pricing
from pricing import discounts as discounts_for
from pricing import line_amount as amount_of


CASES = {
    "test_line_amount": [(amount_of, ("2.50", 4), {}, D("10.00"))],
    "test_subtotal": [(pricing.subtotal, ([("2.50", 4), ("1.00", 3)],), {}, D("13.00"))],
    "test_member_subtotal": [(pricing.subtotal, ([("10.00", 4)],), {"member": True}, D("38.00"))],
    "test_order_total_adds_tax": [(pricing.order_total, ([("10.00", 6)],), {}, D("64.80"))],
    "test_line_amount_is_rounded_half_even": [
        (amount_of, ("0.125", 1), {}, D("0.12")),
        (amount_of, ("0.135", 1), {}, D("0.14")),
        (amount_of, ("0.3333", 3), {}, D("1.00")),
    ],
    "test_small_order_pays_shipping": [(pricing.order_total, ([("10.00", 2)],), {}, D("26.55"))],
    "test_bulk_discount": [
        (amount_of, ("2.00", 10), {}, D("18.00")),
        (amount_of, ("2.00", 9), {}, D("18.00")),
        (amount_of, ("3.00", 20), {}, D("54.00")),
    ],
    "test_member_discount_needs_a_minimum": [
        (pricing.subtotal, ([("5.00", 3)],), {"member": True}, D("15.00")),
        (pricing.subtotal, ([("10.00", 2)],), {"member": True}, D("19.00")),
    ],
    "test_discounts_in_words": [
        (discounts_for, ([("2.00", 10), ("1.00", 1)],), {"member": True}, ["bulk: line 1"]),
        (discounts_for, ([("1.00", 1), ("1.00", 12), ("10.00", 2)],), {"member": True},
         ["bulk: line 2", "member"]),
    ],
}


class Pricing(unittest.TestCase):
    """Both branches' tests, by name, driven from one table of cases."""

    def run_cases(self, name):
        for func, args, kwargs, want in CASES[name]:
            with self.subTest(func=func.__name__, args=args):
                self.assertEqual(want, func(*args, **kwargs))

    def test_line_amount(self):
        self.run_cases("test_line_amount")

    def test_subtotal(self):
        self.run_cases("test_subtotal")

    def test_member_subtotal(self):
        self.run_cases("test_member_subtotal")

    def test_order_total_adds_tax(self):
        self.run_cases("test_order_total_adds_tax")

    def test_line_amount_is_rounded_half_even(self):
        self.run_cases("test_line_amount_is_rounded_half_even")

    def test_shipping(self):
        self.assertEqual(pricing.shipping(D("49.99")), D("4.95"))
        self.assertEqual(pricing.shipping(D("50.00")), D("0.00"))

    def test_small_order_pays_shipping(self):
        self.run_cases("test_small_order_pays_shipping")

    def test_bulk_discount(self):
        self.run_cases("test_bulk_discount")

    def test_member_discount_needs_a_minimum(self):
        self.run_cases("test_member_discount_needs_a_minimum")

    def test_discounts_in_words(self):
        self.run_cases("test_discounts_in_words")


if __name__ == "__main__":
    unittest.main()
