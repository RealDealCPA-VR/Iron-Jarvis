import contextlib
import io
import json
import os
import tempfile
import unittest

from orderdesk.cli import main
from orderdesk.credit import apply_store_credit
from orderdesk.customers import parse_customer
from orderdesk.discounts import order_discount_percent, tier_percent
from orderdesk.invoice import render_invoice
from orderdesk.points import points_earned, points_value_cents
from orderdesk.refunds import points_to_take_back
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
        self.assertTrue(text.endswith("Total: $56.00\nPoints earned: 112"))


class PointsTest(unittest.TestCase):
    def test_points_per_tier(self):
        self.assertEqual(points_earned(5670, "bronze"), 56)
        self.assertEqual(points_earned(5500, "silver"), 82)
        self.assertEqual(points_earned(5600, "gold"), 112)

    def test_nothing_for_nothing(self):
        self.assertEqual(points_earned(0, "gold"), 0)

    def test_a_point_is_a_cent(self):
        self.assertEqual(points_value_cents(250), 250)


class RefundPointsTest(unittest.TestCase):
    def test_in_proportion(self):
        self.assertEqual(points_to_take_back(112, 1000, 5600), 20)

    def test_rounded_up(self):
        self.assertEqual(points_to_take_back(112, 1001, 5600), 21)

    def test_never_more_than_earned(self):
        self.assertEqual(points_to_take_back(10, 9999, 5000), 10)


class StoreCreditTest(unittest.TestCase):
    def test_part_of_the_total(self):
        self.assertEqual(apply_store_credit(5600, 1000), (1000, 4600))

    def test_more_credit_than_the_total(self):
        self.assertEqual(apply_store_credit(5600, 9000), (5600, 0))

    def test_an_invoice_with_credit(self):
        text = render_invoice(parse_customer({**ADA, "tier": "gold"}), LINES, coupon="SAVE10", credit_cents=600)
        self.assertTrue(text.endswith("Total: $56.00\nStore credit: -$6.00\nAmount due: $50.00\nPoints earned: 112"))


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

    def test_the_credit_option(self):
        order = {"customer": ADA, "lines": LINES, "credit_cents": 100}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "order.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(order, fh)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main([path, "--credit", "2500"])
        self.assertEqual(code, 0)
        self.assertIn("Store credit: -$25.00\nAmount due: $50.99", out.getvalue())


if __name__ == "__main__":
    unittest.main()
