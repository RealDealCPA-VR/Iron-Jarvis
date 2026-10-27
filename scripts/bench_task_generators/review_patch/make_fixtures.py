"""Step 3 of the review-patch generator (run by ../team_review_patch.py): the
review-patch fixtures (reference solution, decoy, one alternative, four extra
decoys) from MERGED — the loyalty patch brought by hand into the project as it
is now (express shipping, --output), its three wrong edits kept. Writes only
the ``.sol`` files under FIXTURES (tests/fixtures or a scratch copy of it); a
case's NOTES.md is hand-written and kept as it is.

    python make_fixtures.py MERGED FIXTURES

The patch (patch_after) carries three edits that do not match its description:
the discount cap applied to the coupon alone (discounts.py), points counted on
cents instead of whole dollars (points.py), and gold's free shipping from just
OVER $50.00 (shipping.py).
"""
import os
import shutil
import sys

AFTER = sys.argv[1]
FILES = ["README.md", "orderdesk/cli.py", "orderdesk/credit.py", "orderdesk/customers.py", "orderdesk/discounts.py",
         "orderdesk/invoice.py", "orderdesk/points.py", "orderdesk/refunds.py", "orderdesk/report.py",
         "orderdesk/shipping.py", "test_loyalty.py"]
CAP_BUG = "    return min(coupon_percent(coupon_code), MAX_DISCOUNT_PERCENT) + tier_percent(tier)\n"
CAP_FIX = "    return min(coupon_percent(coupon_code) + tier_percent(tier), MAX_DISCOUNT_PERCENT)\n"
POINTS_BUG = "    return total_cents * numerator // denominator // 100\n"
POINTS_FIX = "    return (total_cents // 100) * numerator // denominator\n"
GOLD_BUG = "    if tier == \"gold\" and subtotal_cents > GOLD_FREE_FROM_CENTS:\n"
GOLD_FIX = "    if tier == \"gold\" and subtotal_cents >= GOLD_FREE_FROM_CENTS:\n"
F = sys.argv[2]
ROOTS = {
    "sol": os.path.join(F, "harness_bench_solutions", "team-review-patch"),
    "decoy": os.path.join(F, "harness_bench_decoys", "team-review-patch"),
    "alt": os.path.join(F, "harness_bench_alternatives", "team-review-patch", "fixed-another-way-with-tests"),
    "more_cap": os.path.join(F, "harness_bench_decoys_more", "team-review-patch", "caps-at-the-invoice-only"),
    "more_points": os.path.join(F, "harness_bench_decoys_more", "team-review-patch", "points-still-on-cents"),
    "more_gold": os.path.join(F, "harness_bench_decoys_more", "team-review-patch", "gold-free-only-over-50"),
    "more_express": os.path.join(F, "harness_bench_decoys_more", "team-review-patch", "gold-ships-express-free"),
}


def swap(s, old, new):
    assert s.count(old) == 1, old
    return s.replace(old, new)


def fixed(rel, s, *, cap=True, points=True, gold=True):
    if rel == "orderdesk/discounts.py" and cap:
        s = swap(s, CAP_BUG, CAP_FIX)
    if rel == "orderdesk/points.py" and points:
        s = swap(s, POINTS_BUG, POINTS_FIX)
    if rel == "orderdesk/shipping.py" and gold:
        s = swap(s, GOLD_BUG, GOLD_FIX)
    return s


def sol(rel, s):
    return fixed(rel, s)


def alt(rel, s):
    if rel == "orderdesk/discounts.py":
        s = swap(s, CAP_BUG, "    total = coupon_percent(coupon_code) + tier_percent(tier)\n"
                             "    if total > MAX_DISCOUNT_PERCENT:\n        total = MAX_DISCOUNT_PERCENT\n    return total\n")
    if rel == "orderdesk/points.py":
        s = swap(s, '"""Loyalty points."""\n', '"""Loyalty points."""\n\nfrom fractions import Fraction\n')
        s = swap(s, POINTS_BUG, "    whole_dollars = total_cents // 100\n"
                                "    return int(Fraction(numerator, denominator) * whole_dollars)\n")
    if rel == "orderdesk/shipping.py":
        s = swap(s, GOLD_BUG + "        return 0\n    return 0 if subtotal_cents >= FREE_FROM_CENTS else FLAT_FEE_CENTS\n",
                 "    free_from = GOLD_FREE_FROM_CENTS if tier == \"gold\" else FREE_FROM_CENTS\n"
                 "    return 0 if subtotal_cents >= free_from else FLAT_FEE_CENTS\n")
    if rel == "test_loyalty.py":
        old = ('    def test_never_more_than_25_percent(self):\n'
               '        self.assertEqual(order_discount_percent("SAVE20", "silver"), 25)\n')
        s = swap(s, old, old + '\n    def test_the_cap_is_on_the_total(self):\n'
                               '        # Review: the first version capped the coupon alone (SAVE20 + gold gave 30).\n'
                               '        self.assertEqual(order_discount_percent("SAVE20", "gold"), 25)\n')
        old = ('    def test_gold_ships_free_from_50_dollars(self):\n'
               '        self.assertEqual(shipping_cents(6000, "gold"), 0)\n')
        s = swap(s, old, old + '        # Review: exactly $50.00 is free too.\n'
                               '        self.assertEqual(shipping_cents(5000, "gold"), 0)\n')
        old = '    def test_nothing_for_nothing(self):\n'
        s = swap(s, old, '    def test_whole_dollars_only(self):\n'
                         '        # Review: the first version counted the cents too ($59.99 silver gave 89).\n'
                         '        self.assertEqual(points_earned(5999, "silver"), 88)\n\n' + old)
    if rel == "README.md":
        s = swap(s, "any coupon; a coupon and a tier together never take off more than 25%.",
                 "any coupon; together, a coupon and a tier take off at most 25%.")
    return s


def more_cap(rel, s):
    s = fixed(rel, s, cap=False)
    if rel == "orderdesk/invoice.py":
        s = swap(s, "    percent = order_discount_percent(coupon, customer.tier)\n",
                 "    percent = min(order_discount_percent(coupon, customer.tier), MAX_DISCOUNT_PERCENT)\n")
        s = swap(s, "from .discounts import discount_cents, order_discount_percent",
                 "from .discounts import MAX_DISCOUNT_PERCENT, discount_cents, order_discount_percent")
    return s


def more_points(rel, s):
    return fixed(rel, s, points=False)


def more_gold(rel, s):
    return fixed(rel, s, gold=False)


EXPRESS_FIRST = """    if express:
        return EXPRESS_FEE_CENTS
    if tier == "gold" and subtotal_cents >= GOLD_FREE_FROM_CENTS:
        return 0
"""


def more_express(rel, s):
    s = fixed(rel, s)
    if rel == "orderdesk/shipping.py":
        s = swap(s, EXPRESS_FIRST, """    if tier == "gold" and subtotal_cents >= GOLD_FREE_FROM_CENTS:
        return 0
    if express:
        return EXPRESS_FEE_CENTS
""")
    return s


notes = {}
for key, root in ROOTS.items():
    path = os.path.join(root, "NOTES.md")
    if os.path.exists(path):
        notes[key] = open(path, encoding="utf-8").read()
    shutil.rmtree(root, ignore_errors=True)
for key, transform in (("sol", sol), ("decoy", None), ("alt", alt), ("more_cap", more_cap),
                       ("more_points", more_points), ("more_gold", more_gold), ("more_express", more_express)):
    for rel in FILES:
        s = open(os.path.join(AFTER, rel), encoding="utf-8").read()
        if transform:
            s = transform(rel, s)
        out = os.path.join(ROOTS[key], rel + ".sol")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        open(out, "w", encoding="utf-8", newline="\n").write(s)
    if key in notes:
        open(os.path.join(ROOTS[key], "NOTES.md"), "w", encoding="utf-8", newline="\n").write(notes[key])
print("fixtures written; notes kept:", sorted(notes), file=sys.stderr)
