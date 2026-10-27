"""Step 1 of the review-patch generator (run by ../team_review_patch.py):
writes the colleague's git format-patch file — the diff from BEFORE (the
project as it was when the change was made) to AFTER (the change, its three
wrong edits included).

    python make_patch.py BEFORE AFTER OUT_PATCH
"""
import difflib
import sys
from pathlib import Path

BEFORE, AFTER, OUT_PATCH = (Path(a) for a in sys.argv[1:4])

HEADER = """From 5b2c9e17d0a4f3e8c1a6b9d2e7f4a1c3b8d6e0f2 Mon Sep 17 00:00:00 2001
From: Dana Field <dana.field@example.com>
Date: Thu, 22 Oct 2026 16:04:12 +0200
Subject: [PATCH] Add loyalty tiers

Customers now have a loyalty tier: bronze (the default), silver or gold.

* customers.py: Customer gains `tier`. parse_customer reads the
  customer's "tier" in any case, with spaces around it dropped ("Gold",
  " silver "); a missing or empty tier is bronze; any other value is a
  ValueError. normalize_tier does this for everyone.
* discounts.py: tier_percent(tier) is 0 for bronze, 5 for silver and 10
  for gold. order_discount_percent(coupon_code, tier) is the coupon's
  percent plus the tier's percent, but never more than
  MAX_DISCOUNT_PERCENT (25) in total.
* shipping.py: shipping_cents takes the tier; gold customers ship free on
  orders of $50.00 or more, everyone else from $100.00 as before.
* invoice.py: a silver or gold invoice shows "Tier: Silver" / "Tier: Gold"
  right under the customer's line (nothing for bronze); the discount is
  order_discount_percent's and shipping uses the tier.
* cli.py: --tier gives the customer's tier instead of the order file's
  (checked like the file's).
* report.py: sales_by_tier(orders) sums the subtotals per tier, every
  tier present (0 when it has no orders); a customer with no tier is
  bronze.
* points.py (new): points_earned(total_cents, tier) gives an order's
  loyalty points: for every WHOLE dollar of the total after the discount
  (shipping not counted), 1 point for bronze, 1.5 for silver and 2 for
  gold, rounded down to a whole point; nothing for a total of 0 or less.
  points_value_cents(points): a point is worth one cent.
* invoice.py: a silver or gold invoice ends with "Points earned: N" after
  the Total line; a bronze invoice shows no points.
* refunds.py (new): points_to_take_back(points_earned, refunded_cents,
  paid_cents) takes back the order's points in proportion to the refunded
  part of what was paid, rounded UP to a whole point, never more than the
  order earned (nothing for a refund of 0); paid_cents must be more than 0
  (ValueError otherwise).
* credit.py (new): apply_store_credit(amount_cents, credit_cents) returns
  (used_cents, left_to_pay_cents): the customer's store credit pays as much
  of the amount as it can, never more than the amount and never more than
  the credit, and the rest is left to pay; an amount of 0 or less uses no
  credit and leaves nothing to pay; a negative credit is a ValueError.
* invoice.py: render_invoice takes credit_cents (0 by default); whenever
  store credit pays any of the total (some of it or all of it), "Store
  credit: -$X.XX" (the credit used) and "Amount due: $Y.YY" (what is left
  to pay, $0.00 when the credit covers it all) come right after the Total
  line, before any points line; when it pays nothing, neither line is
  there. Store credit does not change the points.
* cli.py: --credit CENTS gives the customer's store credit instead of the
  order file's "credit_cents" (0 when neither gives one); a --credit that
  is not a whole number, or a negative credit, is an error (exit code 2).
* README.md: the tiers, the points and store credit.
* test_loyalty.py: tests for all of it.
---
"""

FILES = ["README.md", "orderdesk/cli.py", "orderdesk/credit.py", "orderdesk/customers.py", "orderdesk/discounts.py",
         "orderdesk/invoice.py", "orderdesk/points.py", "orderdesk/refunds.py", "orderdesk/report.py",
         "orderdesk/shipping.py", "test_loyalty.py"]

chunks = []
stats = []
for rel in FILES:
    before_path = BEFORE / rel
    after_lines = (AFTER / rel).read_text(encoding="utf-8").splitlines(keepends=True)
    if before_path.exists():
        before_lines = before_path.read_text(encoding="utf-8").splitlines(keepends=True)
        diff = list(difflib.unified_diff(before_lines, after_lines, f"a/{rel}", f"b/{rel}", n=3))
        head = [f"diff --git a/{rel} b/{rel}\n"]
    else:
        before_lines = []
        diff = list(difflib.unified_diff(before_lines, after_lines, "/dev/null", f"b/{rel}", n=3))
        head = [f"diff --git a/{rel} b/{rel}\n", "new file mode 100644\n"]
    # difflib writes "--- a/x\t" style headers without dates when no dates are given
    diff = [line.rstrip("\t\n") + "\n" if line.startswith(("---", "+++")) else line for line in diff]
    added = sum(1 for line in diff[2:] if line.startswith("+"))
    removed = sum(1 for line in diff[2:] if line.startswith("-"))
    stats.append((rel, added, removed))
    chunks += head + diff

width = max(len(r) for r, _, _ in stats)
stat_lines = [f" {r.ljust(width)} | {a + d:3d} {'+' * min(a, 40)}{'-' * min(d, 40)}\n" for r, a, d in stats]
total_a = sum(a for _, a, _ in stats)
total_d = sum(d for _, _, d in stats)
summary = f" {len(stats)} files changed, {total_a} insertions(+), {total_d} deletions(-)\n"
created = "".join(f" create mode 100644 {rel}\n" for rel in FILES if not (BEFORE / rel).exists())
text = HEADER + "".join(stat_lines) + summary + created + "\n" + "".join(chunks) + "-- \n2.47.0\n\n"
OUT_PATCH.parent.mkdir(parents=True, exist_ok=True)
OUT_PATCH.write_text(text, encoding="utf-8", newline="\n")
print("wrote", OUT_PATCH.name, len(text.splitlines()), "lines;", total_a, "+", total_d, "-", file=sys.stderr)
