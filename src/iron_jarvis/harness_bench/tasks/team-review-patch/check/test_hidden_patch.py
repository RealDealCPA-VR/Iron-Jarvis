# IJ-BENCH-HIDDEN-CHECK team-review-patch
"""Hidden check: the change in incoming/0001-Add-loyalty-tiers.patch is in the
code, every point of its description holds (including the cases its own tests
do not cover), the project's own newer features (express shipping, --output)
still work with it, its tests are kept, and the older tests are unchanged."""
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CRLF = bytes([13, 10])
LF = bytes([10])
UNCHANGED = {
    "test_cli.py": "90d9b6283b64868aae5e4e24f04905541a034d2c7dba8156396e77cf37d20732",
    "test_customers.py": "5d46af1df3858749cc4d9bd16bfcc9125bdd9c01824fb5dc629566c89c0d1d5e",
    "test_discounts.py": "030ffdc728c85a98f2919cab8a4929a5541a6327823974c7503635bfefa9f29a",
    "test_invoice.py": "d8bcc9684050ed45275e8fd60b401b70419bbbcf8b36a635a7505f87b477d7d1",
    "test_pricing.py": "333cbe074a9c32fa71a63b0c3da31bc73198d372de8b9db41dd4515d68136576",
    "test_report.py": "ab23820d114a01c974ddf7cc379b454d4a106289594b65b836fe185eb2e6c5a5",
    "test_shipping.py": "d38868586ae9f87d4730b0c0eaa13a929cdc197c7d1482c176fce99ceb484b48",
}
PATCH_TESTS = [
    "test_any_case_and_spaces", "test_the_default_is_bronze", "test_an_unknown_tier", "test_tier_percent",
    "test_coupon_and_tier_add_up", "test_never_more_than_25_percent", "test_gold_ships_free_from_50_dollars",
    "test_the_others_are_unchanged", "test_the_tier_line", "test_no_tier_line_for_bronze", "test_a_gold_invoice",
    "test_sales_by_tier", "test_the_tier_option", "test_points_per_tier", "test_nothing_for_nothing",
    "test_a_point_is_a_cent", "test_in_proportion", "test_rounded_up", "test_never_more_than_earned",
    "test_part_of_the_total", "test_more_credit_than_the_total", "test_an_invoice_with_credit",
    "test_the_credit_option",
]
ADA = {"id": "c-17", "name": "Ada Byron", "email": "ada@example.com"}
LINES = [{"name": "Notebook", "price_cents": 1250, "qty": 2}, {"name": "Fountain pen", "price_cents": 4500, "qty": 1}]


class HiddenDiscounts(unittest.TestCase):
    def test_a_the_cap_is_on_the_total(self):
        from orderdesk import discounts

        self.assertEqual(discounts.MAX_DISCOUNT_PERCENT, 25)
        cases = [
            ((None, "bronze"), 0), (("WELCOME5", "bronze"), 5), ((None, "gold"), 10), (("SAVE10", "silver"), 15),
            (("SAVE10", "gold"), 20), (("SAVE20", "silver"), 25), (("SAVE20", "gold"), 25),
            ((" save20 ", "gold"), 25), (("WELCOME5", "gold"), 15), (("SAVE20", "bronze"), 20),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(discounts.order_discount_percent(*args), expected)

    def test_b_tiers_and_coupons(self):
        from orderdesk import discounts

        self.assertEqual([discounts.tier_percent(t) for t in ("bronze", "silver", "gold")], [0, 5, 10])
        with self.assertRaises(ValueError):
            discounts.order_discount_percent("FREE", "gold")


class HiddenShipping(unittest.TestCase):
    def test_free_from_50_for_gold_only(self):
        from orderdesk.shipping import shipping_cents

        cases = [
            ((5000, "gold"), 0), ((4999, "gold"), 599), ((5000, "silver"), 599), ((9999, "silver"), 599),
            ((10000, "silver"), 0), ((10000, "bronze"), 0), ((9999, "bronze"), 599), ((10000,), 0), ((9999,), 599),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(shipping_cents(*args), expected)

    def test_express_is_never_free(self):
        from orderdesk.shipping import shipping_cents

        for args in ((25000,), (6000, "gold"), (5000, "gold"), (25000, "gold"), (10000, "silver"), (100, "bronze")):
            with self.subTest(args=args):
                self.assertEqual(shipping_cents(*args, express=True), 1299)
        self.assertEqual(shipping_cents(6000, "gold", express=False), 0)


class HiddenCustomers(unittest.TestCase):
    def test_a_tiers(self):
        from orderdesk.customers import parse_customer

        for written, tier in (("GOLD", "gold"), ("  Silver  ", "silver"), ("", "bronze"), ("bronze", "bronze")):
            with self.subTest(tier=written):
                self.assertEqual(parse_customer({**ADA, "tier": written}).tier, tier)
        self.assertEqual(parse_customer(ADA).tier, "bronze")
        for bad in ("platinum", "golden", "gold-plus"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_customer({**ADA, "tier": bad})

    def test_b_normalize_tier_and_the_default(self):
        from orderdesk.customers import Customer, normalize_tier

        self.assertEqual(normalize_tier(None), "bronze")
        self.assertEqual(normalize_tier(" GOLD"), "gold")
        with self.assertRaises(ValueError):
            normalize_tier("diamond")
        self.assertEqual(Customer("c-1", "Ada", "ada@example.com").tier, "bronze")


class HiddenInvoice(unittest.TestCase):
    def render(self, tier=None, coupon=None):
        from orderdesk.customers import parse_customer
        from orderdesk.invoice import render_invoice

        row = dict(ADA) if tier is None else {**ADA, "tier": tier}
        return render_invoice(parse_customer(row), LINES, coupon=coupon).splitlines()

    def test_a_the_cap_on_an_invoice(self):
        lines = self.render("gold", "SAVE20")
        self.assertIn("Discount (25%): -$17.50", lines)
        self.assertEqual(lines[-3:], ["Shipping: free", "Total: $52.50", "Points earned: 104"])

    def test_b_a_whole_silver_invoice(self):
        self.assertEqual(self.render("Silver", "save10"), [
            "Invoice for Ada Byron <ada@example.com>",
            "Tier: Silver",
            "  2 x Notebook: $25.00",
            "  1 x Fountain pen: $45.00",
            "Subtotal: $70.00",
            "Discount (15%): -$10.50",
            "Shipping: $5.99",
            "Total: $65.49",
            "Points earned: 88",
        ])

    def test_d_express_with_a_tier_and_credit(self):
        from orderdesk.customers import parse_customer
        from orderdesk.invoice import render_invoice

        gold = parse_customer({**ADA, "tier": "gold"})
        self.assertEqual(render_invoice(gold, LINES, coupon="SAVE20", express=True).splitlines()[-4:], [
            "Discount (25%): -$17.50", "Shipping (express): $12.99", "Total: $65.49", "Points earned: 104",
        ])
        silver = parse_customer({**ADA, "tier": "silver"})
        self.assertEqual(render_invoice(silver, LINES, credit_cents=10000, express=True).splitlines()[-5:], [
            "Shipping (express): $12.99", "Total: $79.49", "Store credit: -$79.49", "Amount due: $0.00",
            "Points earned: 99",
        ])
        self.assertEqual(render_invoice(silver, LINES).splitlines()[-3:-1], ["Shipping: $5.99", "Total: $72.49"])

    def test_c_bronze_is_as_before(self):
        self.assertEqual(self.render(), [
            "Invoice for Ada Byron <ada@example.com>",
            "  2 x Notebook: $25.00",
            "  1 x Fountain pen: $45.00",
            "Subtotal: $70.00",
            "Shipping: $5.99",
            "Total: $75.99",
        ])


class HiddenCli(unittest.TestCase):
    def run_cli(self, order, *args):
        from orderdesk.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "order.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(order, fh)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main([path, *args])
            return code, out.getvalue(), err.getvalue()

    def test_a_the_option_wins_over_the_file(self):
        code, out, _ = self.run_cli({"customer": {**ADA, "tier": "silver"}, "lines": LINES}, "--tier", "gold")
        self.assertEqual(code, 0)
        self.assertIn("Tier: Gold", out)
        self.assertIn("Shipping: free", out)

    def test_b_the_files_tier(self):
        code, out, _ = self.run_cli({"customer": {**ADA, "tier": "Silver"}, "lines": LINES, "coupon": "SAVE20"})
        self.assertEqual(code, 0)
        self.assertIn("Tier: Silver", out)
        self.assertIn("Discount (25%): -$17.50", out)

    def test_c_a_wrong_tier_is_an_error(self):
        code, out, err = self.run_cli({"customer": ADA, "lines": LINES}, "--tier", "platinum")
        self.assertEqual(code, 2)
        self.assertIn("error:", err)
        code, _, err = self.run_cli({"customer": {**ADA, "tier": "wood"}, "lines": LINES})
        self.assertEqual(code, 2)

    def test_d_store_credit(self):
        order = {"customer": {**ADA, "tier": "silver"}, "lines": LINES, "credit_cents": 1000}
        code, out, _ = self.run_cli(order)
        self.assertEqual(code, 0)
        self.assertTrue(out.rstrip().endswith(
            "Total: $72.49\nStore credit: -$10.00\nAmount due: $62.49\nPoints earned: 99"), out)
        code, out, _ = self.run_cli(order, "--credit", "9000")
        self.assertEqual(code, 0)
        self.assertIn("Store credit: -$72.49\nAmount due: $0.00", out)
        code, out, _ = self.run_cli(order, "--credit", "0")
        self.assertEqual(code, 0)
        self.assertNotIn("Store credit", out)
        self.assertNotIn("Amount due", out)
        code, out, _ = self.run_cli({"customer": ADA, "lines": LINES})
        self.assertEqual(code, 0)
        self.assertNotIn("Store credit", out)

    def test_f_output_and_express_with_the_new_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "out", "invoice.txt")
            os.makedirs(os.path.dirname(target))
            code, out, _ = self.run_cli({"customer": ADA, "lines": LINES}, "--tier", "gold", "--credit", "500",
                                        "--express", "--output", target)
            self.assertEqual(code, 0)
            self.assertEqual(out.strip(), f"wrote {target}")
            with open(target, encoding="utf-8") as fh:
                written = fh.read().splitlines()
        self.assertEqual(written[1], "Tier: Gold")
        self.assertEqual(written[-5:], [
            "Shipping (express): $12.99", "Total: $75.99", "Store credit: -$5.00", "Amount due: $70.99",
            "Points earned: 126",
        ])

    def test_e_a_wrong_credit_is_an_error(self):
        for args, order_credit in ((("--credit", "-5"), None), (("--credit", "ten"), None), (("--credit", "12.5"), None),
                                   ((), -300)):
            with self.subTest(args=args, order_credit=order_credit):
                order = {"customer": ADA, "lines": LINES}
                if order_credit is not None:
                    order["credit_cents"] = order_credit
                code, out, err = self.run_cli(order, *args)
                self.assertEqual(code, 2)
                self.assertIn("error:", err)
                self.assertEqual(out, "")


class HiddenReport(unittest.TestCase):
    def test_sales_by_tier(self):
        from orderdesk.report import sales_by_tier

        orders = [
            {"customer": {**ADA, "tier": "GOLD"}, "lines": LINES},
            {"customer": {**ADA, "tier": " gold"}, "lines": [{"name": "Ink", "price_cents": 800, "qty": 2}]},
            {"customer": ADA, "lines": [{"name": "Ink", "price_cents": 800, "qty": 1}]},
            {"lines": [{"name": "Pen", "price_cents": 300, "qty": 1}]},
        ]
        self.assertEqual(sales_by_tier(orders), {"bronze": 1100, "silver": 0, "gold": 8600})
        self.assertEqual(sales_by_tier([]), {"bronze": 0, "silver": 0, "gold": 0})


class HiddenPoints(unittest.TestCase):
    def test_a_points_earned(self):
        from orderdesk.points import points_earned, points_value_cents

        cases = [
            ((5999, "silver"), 88), ((99, "gold"), 0), ((100, "silver"), 1), ((300, "silver"), 4),
            ((-500, "gold"), 0), ((0, "bronze"), 0), ((12345, "bronze"), 123), ((12345, "gold"), 246),
            ((12345, "silver"), 184),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(points_earned(*args), expected)
        self.assertEqual(points_value_cents(7), 7)

    def test_b_points_taken_back(self):
        from orderdesk.refunds import points_to_take_back

        cases = [
            ((112, 1000, 5600), 20), ((112, 1001, 5600), 21), ((10, 9999, 5000), 10), ((0, 500, 1000), 0),
            ((7, 1, 1000), 1), ((100, 0, 1000), 0), ((88, 5950, 5950), 88), ((88, 2975, 5950), 44),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(points_to_take_back(*args), expected)
        with self.assertRaises(ValueError):
            points_to_take_back(10, 100, 0)


class HiddenCredit(unittest.TestCase):
    def test_a_apply_store_credit(self):
        from orderdesk.credit import apply_store_credit

        cases = [
            ((5600, 1000), (1000, 4600)), ((5600, 9000), (5600, 0)), ((5600, 5600), (5600, 0)), ((5600, 0), (0, 5600)),
            ((0, 500), (0, 0)), ((-250, 500), (0, 0)), ((1, 1), (1, 0)), ((7599, 7600), (7599, 0)),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(tuple(apply_store_credit(*args)), expected)
        with self.assertRaises(ValueError):
            apply_store_credit(5600, -1)

    def render(self, tier, credit, coupon=None):
        from orderdesk.customers import parse_customer
        from orderdesk.invoice import render_invoice

        row = dict(ADA) if tier is None else {**ADA, "tier": tier}
        return render_invoice(parse_customer(row), LINES, coupon=coupon, credit_cents=credit).splitlines()

    def test_b_a_gold_invoice_with_credit(self):
        self.assertEqual(self.render("gold", 2550, "SAVE20")[-5:], [
            "Shipping: free", "Total: $52.50", "Store credit: -$25.50", "Amount due: $27.00", "Points earned: 104",
        ])

    def test_c_a_bronze_invoice_with_credit(self):
        self.assertEqual(self.render(None, 10000)[-4:], [
            "Shipping: $5.99", "Total: $75.99", "Store credit: -$75.99", "Amount due: $0.00",
        ])

    def test_d_no_credit_no_lines(self):
        for tier in (None, "silver"):
            with self.subTest(tier=tier):
                lines = self.render(tier, 0)
                self.assertFalse([line for line in lines if line.startswith(("Store credit", "Amount due"))])

    def test_e_a_negative_credit(self):
        with self.assertRaises(ValueError):
            self.render("gold", -100)


class HiddenKept(unittest.TestCase):
    def test_a_the_patch_tests_are_kept(self):
        path = os.path.join(HERE, "test_loyalty.py")
        self.assertTrue(os.path.isfile(path), "test_loyalty.py (added by the patch) is missing")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        missing = [name for name in PATCH_TESTS if not re.search(rf"def {name}\b", text)]
        self.assertEqual(missing, [], "tests of the patch are missing from test_loyalty.py")

    def test_b_the_readme_documents_the_tiers(self):
        with open(os.path.join(HERE, "README.md"), encoding="utf-8") as fh:
            text = fh.read().lower()
        for word in ("bronze", "silver", "gold", "--tier", "--credit", "store credit", "--express", "--output"):
            self.assertIn(word, text, f"README.md does not mention {word}")

    def test_c_the_older_tests_are_unchanged(self):
        for name, digest in UNCHANGED.items():
            path = os.path.join(HERE, name)
            self.assertTrue(os.path.isfile(path), f"{name} was removed")
            with open(path, "rb") as fh:
                data = fh.read().replace(CRLF, LF)
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest, f"{name} was changed")


if __name__ == "__main__":
    unittest.main()
