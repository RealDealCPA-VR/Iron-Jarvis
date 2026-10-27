import contextlib
import io
import json
import os
import tempfile
import unittest

from orderdesk.cli import main
from orderdesk.customers import parse_customer
from orderdesk.discounts import order_discount_percent, tier_percent
from orderdesk.invoice import render_invoice
from orderdesk.report import sales_by_tier
from orderdesk.shipping import shipping_cents

ADA = {"id": "c-17", "name": "Ada Byron", "email": "ada@example.com"}
LINES = [{"name": "Notebook", "price_cents": 1250, "qty": 2}, {"name": "Fountain pen", "price_cents": 4500, "qty": 1}]


class TierParsingTest(unittest.TestCase):
    def test_any_case_and_spaces(self):
        self.assertEqual(parse_customer({**ADA, "tier": "Gold"}).tier, "gold")
        self.assertEqual(parse_customer({**ADA, "tier": " silver "}).tier, "silver")

    def test_the_default_is_bronze(self):
        self.assertEqual(parse_customer(ADA).tier, "bronze")

    def test_an_unknown_tier(self):
        with self.assertRaises(ValueError):
            parse_customer({**ADA, "tier": "platinum"})


class TierDiscountTest(unittest.TestCase):
    def test_tier_percent(self):
        self.assertEqual([tier_percent(t) for t in ("bronze", "silver", "gold")], [0, 5, 10])

    def test_coupon_and_tier_add_up(self):
        self.assertEqual(order_discount_percent("SAVE10", "gold"), 20)
        self.assertEqual(order_discount_percent(None, "silver"), 5)

    def test_never_more_than_25_percent(self):
        self.assertEqual(order_discount_percent("SAVE20", "silver"), 25)


class GoldShippingTest(unittest.TestCase):
    def test_gold_ships_free_from_50_dollars(self):
        self.assertEqual(shipping_cents(6000, "gold"), 0)
        self.assertEqual(shipping_cents(4000, "gold"), 599)

    def test_the_others_are_unchanged(self):
        self.assertEqual(shipping_cents(6000, "silver"), 599)
        self.assertEqual(shipping_cents(10000, "silver"), 0)


class InvoiceTierTest(unittest.TestCase):
    def test_the_tier_line(self):
        lines = render_invoice(parse_customer({**ADA, "tier": "gold"}), LINES).splitlines()
        self.assertEqual(lines[1], "Tier: Gold")

    def test_no_tier_line_for_bronze(self):
        self.assertNotIn("Tier:", render_invoice(parse_customer(ADA), LINES))

    def test_a_gold_invoice(self):
        text = render_invoice(parse_customer({**ADA, "tier": "gold"}), LINES, coupon="SAVE10")
        self.assertIn("Discount (20%): -$14.00", text)
        self.assertIn("Shipping: free", text)
        self.assertTrue(text.endswith("Total: $56.00"))


class ReportTierTest(unittest.TestCase):
    def test_sales_by_tier(self):
        orders = [
            {"customer": {**ADA, "tier": "gold"}, "lines": LINES},
            {"customer": ADA, "lines": [{"name": "Ink", "price_cents": 800, "qty": 1}]},
        ]
        self.assertEqual(sales_by_tier(orders), {"bronze": 800, "silver": 0, "gold": 7000})


class CliTierTest(unittest.TestCase):
    def test_the_tier_option(self):
        order = {"customer": ADA, "lines": [{"name": "Notebook", "price_cents": 1250, "qty": 5}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "order.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(order, fh)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main([path, "--tier", "gold"])
        self.assertEqual(code, 0)
        self.assertIn("Tier: Gold", out.getvalue())
        self.assertIn("Shipping: free", out.getvalue())


if __name__ == "__main__":
    unittest.main()
