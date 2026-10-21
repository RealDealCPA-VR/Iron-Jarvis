# IJ-BENCH-HIDDEN-CHECK hard-cross-module-bug
import unittest

from money import format_cents, to_cents
from order import Order
from report import order_report
from tax import tax_cents

CASES = [
    (19.99, 1999), (0.29, 29), (4.35, 435), (34.95, 3495), (10.0, 1000), (3, 300),
    (2.675, 268), (0.125, 13), (-0.125, -13), (1.005, 101), (-1.005, -101),
    (0.005, 1), (0.004, 0), (0.1 + 0.2, 30), ("4.35", 435), ("0.125", 13), ("-2.675", -268),
]


class HiddenMoney(unittest.TestCase):
    def test_to_cents_follows_the_rules(self):
        for amount, cents in CASES:
            with self.subTest(amount=amount):
                self.assertEqual(to_cents(amount), cents)
                self.assertIs(type(to_cents(amount)), int)

    def test_orders_use_the_rule(self):
        self.assertEqual(Order().add("SAMPLE", 3).subtotal_cents(), 39)
        self.assertEqual(Order().add("FILTER", 2).add("TRAY").subtotal_cents(), 971)

    def test_tax_uses_the_rule(self):
        self.assertEqual(tax_cents(1000, "TX"), 83)
        self.assertEqual(tax_cents(4085, "TX"), 337)
        self.assertEqual(tax_cents(3495, "NY"), 310)

    def test_the_report(self):
        text = order_report(Order().add("GIFTCARD"), "TX")
        self.assertIn("Tax (TX): 0.83", text)
        self.assertTrue(text.endswith("Total: 10.83"))
        text = order_report(Order().add("FILTER").add("SAMPLE", 2), "TX")
        self.assertIn("FILTER      1 x    2.68 =     2.68", text)
        self.assertIn("Subtotal: 2.94", text)

    def test_formatting_unchanged(self):
        self.assertEqual(format_cents(12345), "123.45")
        self.assertEqual(format_cents(-5), "-0.05")
        self.assertEqual(format_cents(0), "0.00")
