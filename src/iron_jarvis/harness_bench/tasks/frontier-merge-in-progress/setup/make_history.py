# IJ-BENCH-HIDDEN-CHECK frontier-merge-in-progress (setup step: hidden, never copied to the agent)
"""Turn the copied project into a repository in the middle of a merge.

    python make_history.py <folder> <seed>

The copied files (CRLF folded to LF) are the first commit on ``main``. Two
commits on ``feature/bulk-discount`` and two more on ``main`` follow, written
from the contents below with FIXED authors and dates, so every commit id is
the same on every run and every machine (the seed is not needed: the history
is fixed). Then ``git merge --no-ff feature/bulk-discount`` is started on
``main`` and stops on its conflicts — the state the agent finds.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ADA = ("Ada Lovelace", "ada@example.com")
GRACE = ("Grace Hopper", "grace@example.com")

HEADER = '''"""Order pricing for the shop.

An order is a list of lines; a line is ``(unit_price, quantity)`` with the
price as a string or a Decimal. README.md states the pricing rules.
"""
'''

# --------------------------------------------------------------------------- #
# feature/bulk-discount
# --------------------------------------------------------------------------- #

F1_PRICING = HEADER + '''
from decimal import ROUND_HALF_UP, Decimal

TAX_RATE = Decimal("0.08")
MEMBER_DISCOUNT = Decimal("0.05")
CENT = Decimal("0.01")
BULK_MIN_QUANTITY = 10
BULK_DISCOUNT = Decimal("0.10")


def line_amount(unit_price, quantity):
    """The amount for one line. A line of BULK_MIN_QUANTITY units or more
    gets BULK_DISCOUNT off its amount (a per-line adjustment)."""
    amount = Decimal(unit_price) * quantity
    if quantity >= BULK_MIN_QUANTITY:
        amount -= amount * BULK_DISCOUNT
    return amount


def subtotal(lines, *, member=False):
    """The sum of the lines' amounts, less the member discount."""
    total = sum((line_amount(price, qty) for price, qty in lines), Decimal("0"))
    if member:
        total -= total * MEMBER_DISCOUNT
    return total


def order_total(lines, *, member=False):
    """What the customer pays: the subtotal plus tax, rounded to cents."""
    base = subtotal(lines, member=member)
    return (base * (1 + TAX_RATE)).quantize(CENT, rounding=ROUND_HALF_UP)
'''

F2_PRICING = HEADER + '''
from decimal import ROUND_HALF_UP, Decimal

TAX_RATE = Decimal("0.08")
MEMBER_DISCOUNT = Decimal("0.05")
CENT = Decimal("0.01")
BULK_MIN_QUANTITY = 10
BULK_DISCOUNT = Decimal("0.10")
MEMBER_MINIMUM = Decimal("20.00")


def line_amount(unit_price, quantity):
    """The amount for one line. A line of BULK_MIN_QUANTITY units or more
    gets BULK_DISCOUNT off its amount (a per-line adjustment)."""
    amount = Decimal(unit_price) * quantity
    if quantity >= BULK_MIN_QUANTITY:
        amount -= amount * BULK_DISCOUNT
    return amount


def subtotal(lines, *, member=False):
    """The sum of the lines' amounts, less the member discount (only when
    that sum is MEMBER_MINIMUM or more)."""
    total = sum((line_amount(price, qty) for price, qty in lines), Decimal("0"))
    if member and total >= MEMBER_MINIMUM:
        total -= total * MEMBER_DISCOUNT
    return total


def order_total(lines, *, member=False):
    """What the customer pays: the subtotal plus tax, rounded to cents."""
    base = subtotal(lines, member=member)
    return (base * (1 + TAX_RATE)).quantize(CENT, rounding=ROUND_HALF_UP)


def discounts(lines, *, member=False):
    """The discounts an order gets, in words and in this order: one entry
    per bulk line ("bulk: line N", counting lines from 1), then "member"
    when the member discount applies."""
    out = [f"bulk: line {i}" for i, (_price, qty) in enumerate(lines, start=1) if qty >= BULK_MIN_QUANTITY]
    gross = sum((line_amount(price, qty) for price, qty in lines), Decimal("0"))
    if member and gross >= MEMBER_MINIMUM:
        out.append("member")
    return out
'''

F2_RECEIPTS = '''"""A printed receipt for an order."""

from pricing import discounts, order_total


def receipt(lines, *, member=False):
    """One line per order line, one per discount, then the total."""
    out = [f"{qty} x {price}" for price, qty in lines]
    out += [f"discount: {name}" for name in discounts(lines, member=member)]
    out.append(f"total: {order_total(lines, member=member)}")
    return "\\n".join(out)
'''

TESTS_HEAD = '''import unittest
from decimal import Decimal

from pricing import {names}


class Pricing(unittest.TestCase):
    def test_line_amount(self):
        self.assertEqual(line_amount("2.50", 4), Decimal("10.00"))

    def test_subtotal(self):
        self.assertEqual(subtotal([("2.50", 4), ("1.00", 3)]), Decimal("13.00"))

    def test_member_subtotal(self):
        self.assertEqual(subtotal([("10.00", 4)], member=True), Decimal("38.00"))

    def test_order_total_adds_tax(self):
        self.assertEqual(order_total([("10.00", 6)]), Decimal("64.80"))
'''
TESTS_TAIL = '''

if __name__ == "__main__":
    unittest.main()
'''
F1_TESTS_ADD = '''
    def test_bulk_discount(self):
        self.assertEqual(line_amount("2.00", 10), Decimal("18.00"))
        self.assertEqual(line_amount("2.00", 9), Decimal("18.00"))
        self.assertEqual(line_amount("3.00", 20), Decimal("54.00"))
'''
F2_TESTS_ADD = '''
    def test_member_discount_needs_a_minimum(self):
        self.assertEqual(subtotal([("5.00", 3)], member=True), Decimal("15.00"))
        self.assertEqual(subtotal([("10.00", 2)], member=True), Decimal("19.00"))

    def test_discounts_in_words(self):
        self.assertEqual(discounts([("2.00", 10), ("1.00", 1)], member=True), ["bulk: line 1"])
        self.assertEqual(discounts([("1.00", 1), ("1.00", 12), ("10.00", 2)], member=True),
                         ["bulk: line 2", "member"])
'''
F1_TESTS = TESTS_HEAD.format(names="line_amount, order_total, subtotal") + F1_TESTS_ADD + TESTS_TAIL
F2_TESTS = (TESTS_HEAD.format(names="discounts, line_amount, order_total, subtotal")
            + F1_TESTS_ADD + F2_TESTS_ADD + TESTS_TAIL)

README_HEAD = '''# shop-pricing

What the shop charges for an order. The code is `pricing.py`; the tests are
`test_pricing.py` (run them with `python -m unittest`).

## Pricing rules

'''
README_TAIL = '''
## Changes

Every change is listed in CHANGELOG.md, under "Unreleased" until it ships.
'''
RULE1 = "1. A line's amount is its unit price times its quantity.\n"
RULE1_F1 = ("1. A line's amount is its unit price times its quantity. A line of 10 or more\n"
            "   units gets 10% off its amount (a per-line adjustment).\n")
RULE1_M2 = ("1. A line's amount is its unit price times its quantity, after any per-line\n"
            "   adjustment, rounded to cents (half-even) as the last step.\n")
RULE2 = "2. Members get 5% off the subtotal (the sum of the lines' amounts).\n"
RULE2_F2 = ("2. Members get 5% off the subtotal (the sum of the lines' amounts) when that\n"
            "   sum is 20.00 or more.\n")
RULE3 = "3. Tax (8%) is added to the subtotal; the result is rounded to cents, half up.\n"
RULE4_M3 = ("4. Shipping costs 4.95, and is free when the subtotal (after the member\n"
            "   discount) is 50.00 or more. Shipping is not taxed.\n")

CHANGELOG_BASE = "\n## 1.0.0\n\n- First release: line amounts, the member discount and tax.\n"


def changelog(*bullets: str) -> str:
    return "# Changelog\n\n## Unreleased\n\n" + "".join(f"- {b}\n" for b in bullets) + CHANGELOG_BASE


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

M2_LINE = '''def line_amount(unit_price, quantity):
    """The amount for one line, rounded to cents (half-even).

    Rounding is the LAST step of a line's amount: any per-line adjustment is
    applied before it, so a line's amount is always whole cents.
    """
    amount = (Decimal(unit_price) * quantity).quantize(CENT, rounding=ROUND_HALF_EVEN)
    return amount
'''


def base_pricing(work: Path) -> str:
    return (work / "pricing.py").read_text(encoding="utf-8")


def m2_pricing(base: str) -> str:
    out = base.replace("from decimal import ROUND_HALF_UP, Decimal",
                       "from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal")
    old_line = base[base.index("def line_amount("):base.index("def subtotal(")]
    return out.replace(old_line, M2_LINE + "\n\n")


def m3_pricing(m2: str) -> str:
    out = m2.replace('CENT = Decimal("0.01")\n',
                     'CENT = Decimal("0.01")\nSHIPPING_FEE = Decimal("4.95")\nFREE_SHIPPING_FROM = Decimal("50.00")\n')
    old_total = out[out.index("def order_total("):]
    new_total = '''def order_total(lines, *, member=False):
    """What the customer pays: the subtotal plus tax, rounded to cents, plus
    shipping (which is not taxed)."""
    base = subtotal(lines, member=member)
    taxed = (base * (1 + TAX_RATE)).quantize(CENT, rounding=ROUND_HALF_UP)
    return taxed + shipping(base)


def shipping(amount):
    """The shipping fee for a subtotal: free from FREE_SHIPPING_FROM."""
    return Decimal("0.00") if amount >= FREE_SHIPPING_FROM else SHIPPING_FEE
'''
    return out.replace(old_total, new_total)


M2_TESTS_ADD = '''
    def test_line_amount_is_rounded_half_even(self):
        self.assertEqual(line_amount("0.125", 1), Decimal("0.12"))
        self.assertEqual(line_amount("0.135", 1), Decimal("0.14"))
        self.assertEqual(line_amount("0.3333", 3), Decimal("1.00"))
'''
M3_TESTS_ADD = '''
    def test_shipping(self):
        self.assertEqual(shipping(Decimal("49.99")), Decimal("4.95"))
        self.assertEqual(shipping(Decimal("50.00")), Decimal("0.00"))

    def test_small_order_pays_shipping(self):
        self.assertEqual(order_total([("10.00", 2)]), Decimal("26.55"))
'''
M2_TESTS = TESTS_HEAD.format(names="line_amount, order_total, subtotal") + M2_TESTS_ADD + TESTS_TAIL
M3_TESTS = (TESTS_HEAD.format(names="line_amount, order_total, shipping, subtotal")
            + M2_TESTS_ADD + M3_TESTS_ADD + TESTS_TAIL)


def git(work: Path, *args: str, who: tuple[str, str] | None = None, when: str = "", check: bool = True):
    env = dict(os.environ)
    if who:
        env.update({"GIT_AUTHOR_NAME": who[0], "GIT_AUTHOR_EMAIL": who[1],
                    "GIT_COMMITTER_NAME": who[0], "GIT_COMMITTER_EMAIL": who[1]})
    if when:
        env.update({"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})
    result = subprocess.run(["git", *args], cwd=work, env=env, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}")
    return result


def write(work: Path, name: str, text: str) -> None:
    with open(work / name, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def commit(work: Path, message: str, who: tuple[str, str], when: str) -> None:
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", message, who=who, when=when)


def main(argv: list[str]) -> int:
    work = Path(argv[1])
    # The copied project, line endings folded (a checkout may have made them CRLF).
    for path in sorted(work.iterdir()):
        if path.is_file():
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    write(work, ".gitignore", "__pycache__/\n.pytest_cache/\n*.pyc\n")
    git(work, "init", "-q")
    git(work, "symbolic-ref", "HEAD", "refs/heads/main")
    for key, value in (("core.autocrlf", "false"), ("core.eol", "lf"), ("core.filemode", "false"),
                       ("commit.gpgsign", "false"), ("tag.gpgsign", "false"),
                       ("user.name", "Bench User"), ("user.email", "bench@example.com"),
                       ("merge.conflictstyle", "merge"), ("core.hooksPath", ".git/no-hooks")):
        git(work, "config", key, value)
    base = base_pricing(work)
    commit(work, "Pricing module", ADA, "2026-01-05T10:00:00+00:00")
    readme_base = (work / "README.md").read_text(encoding="utf-8")
    assert readme_base == README_HEAD + RULE1 + RULE2 + RULE3 + README_TAIL, "README.md is not the base one"

    git(work, "checkout", "-q", "-b", "feature/bulk-discount")
    write(work, "pricing.py", F1_PRICING)
    write(work, "test_pricing.py", F1_TESTS)
    write(work, "README.md", README_HEAD + RULE1_F1 + RULE2 + RULE3 + README_TAIL)
    write(work, "CHANGELOG.md", changelog("Bulk discount: 10% off a line of 10 or more units."))
    commit(work, "Add a bulk discount for lines of 10 or more units", GRACE, "2026-01-12T14:00:00+00:00")
    write(work, "pricing.py", F2_PRICING)
    write(work, "receipts.py", F2_RECEIPTS)
    write(work, "test_pricing.py", F2_TESTS)
    write(work, "README.md", README_HEAD + RULE1_F1 + RULE2_F2 + RULE3 + README_TAIL)
    write(work, "CHANGELOG.md", changelog("Bulk discount: 10% off a line of 10 or more units.",
                                          "The member discount needs a subtotal of 20.00 or more.",
                                          "Receipts list the discounts an order got."))
    commit(work, "Member discount minimum, discounts in words, receipts", GRACE, "2026-01-13T09:30:00+00:00")

    git(work, "checkout", "-q", "main")
    m2 = m2_pricing(base)
    write(work, "pricing.py", m2)
    write(work, "test_pricing.py", M2_TESTS)
    write(work, "README.md", README_HEAD + RULE1_M2 + RULE2 + RULE3 + README_TAIL)
    write(work, "CHANGELOG.md", changelog("Line amounts are rounded to cents (half-even)."))
    commit(work, "Round each line's amount to cents, half-even", ADA, "2026-01-14T11:00:00+00:00")
    write(work, "pricing.py", m3_pricing(m2))
    write(work, "test_pricing.py", M3_TESTS)
    write(work, "README.md", README_HEAD + RULE1_M2 + RULE2 + RULE3 + RULE4_M3 + README_TAIL)
    write(work, "CHANGELOG.md", changelog("Line amounts are rounded to cents (half-even).",
                                          "Free shipping from 50.00; shipping is 4.95 below that."))
    commit(work, "Shipping: 4.95, free from 50.00", ADA, "2026-01-15T16:45:00+00:00")

    merged = git(work, "merge", "--no-ff", "-m", "Merge branch 'feature/bulk-discount'",
                 "feature/bulk-discount", who=ADA, when="2026-01-16T10:00:00+00:00", check=False)
    if merged.returncode == 0 or not (work / ".git" / "MERGE_HEAD").is_file():
        raise SystemExit("the merge did not stop on its conflicts: " + merged.stdout + merged.stderr)
    unmerged = git(work, "diff", "--name-only", "--diff-filter=U").stdout.split()
    if sorted(unmerged) != ["CHANGELOG.md", "README.md", "pricing.py", "test_pricing.py"]:
        raise SystemExit(f"unexpected conflicts: {unmerged}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
