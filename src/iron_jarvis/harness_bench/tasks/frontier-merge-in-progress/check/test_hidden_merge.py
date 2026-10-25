# IJ-BENCH-HIDDEN-CHECK frontier-merge-in-progress
"""Hidden check: the merge is finished, nothing from either side was lost.

Run as a script (exit 0 passed, 1 failed, 3 when git is not installed: a
check that cannot run is never a fail).
"""
import ast
import importlib
import os
import re
import shutil
import subprocess
import sys
import unittest
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

MAIN_TIP = "dae2c90112795f93c76e8596b999a6c74250c91e"
FEATURE_TIP = "a1e95a34f3a77ffda1bf57a8685da8787f9df50a"
MY_FILES = {os.path.basename(__file__)}
BOTH_SIDES_TESTS = {
    # main
    "test_line_amount_is_rounded_half_even", "test_shipping", "test_small_order_pays_shipping",
    # feature/bulk-discount
    "test_bulk_discount", "test_member_discount_needs_a_minimum", "test_discounts_in_words",
    # the first commit
    "test_line_amount", "test_subtotal", "test_member_subtotal", "test_order_total_adds_tax",
}
#: Every case the two branches' tests (and the first commit's) check, as
#: (function, args, keyword args, expected). No rule of either branch changes
#: any of these values, so a merged test file that keeps the tests keeps them
#: all — in whatever layout (a table, subTest, its own helpers).
BOTH_SIDES_CASES = [
    # the first commit
    ("line_amount", ("2.50", 4), {}, Decimal("10.00")),
    ("subtotal", ([("2.50", 4), ("1.00", 3)],), {}, Decimal("13.00")),
    ("subtotal", ([("10.00", 4)],), {"member": True}, Decimal("38.00")),
    ("order_total", ([("10.00", 6)],), {}, Decimal("64.80")),
    # main
    ("line_amount", ("0.125", 1), {}, Decimal("0.12")),
    ("line_amount", ("0.135", 1), {}, Decimal("0.14")),
    ("line_amount", ("0.3333", 3), {}, Decimal("1.00")),
    ("shipping", (Decimal("49.99"),), {}, Decimal("4.95")),
    ("shipping", (Decimal("50.00"),), {}, Decimal("0.00")),
    ("order_total", ([("10.00", 2)],), {}, Decimal("26.55")),
    # feature/bulk-discount
    ("line_amount", ("2.00", 10), {}, Decimal("18.00")),
    ("line_amount", ("2.00", 9), {}, Decimal("18.00")),
    ("line_amount", ("3.00", 20), {}, Decimal("54.00")),
    ("subtotal", ([("5.00", 3)],), {"member": True}, Decimal("15.00")),
    ("subtotal", ([("10.00", 2)],), {"member": True}, Decimal("19.00")),
    ("discounts", ([("2.00", 10), ("1.00", 1)],), {"member": True}, ["bulk: line 1"]),
    ("discounts", ([("1.00", 1), ("1.00", 12), ("10.00", 2)],), {"member": True}, ["bulk: line 2", "member"]),
]
PRICING_FUNCS = ("line_amount", "subtotal", "order_total", "shipping", "discounts")
#: The rules of both branches, as facts the merged README must still state
#: (any wording): what each rule says, not the sentence it was written in.
README_FACTS = [
    ("the bulk discount (10%)", r"\b10\s*(%|percent)"),
    ("the bulk threshold (10 or more units)", r"\b(10|ten)\b(?!\s*(%|percent|\.\d))"),
    ("each line rounded half-even", r"half[- ]?even|banker"),
    ("the member discount (5%)", r"\b5\s*(%|percent)"),
    ("the member minimum (20.00)", r"\b20\.00\b"),
    ("tax (8%)", r"\b8\s*(%|percent)"),
    ("the total rounded half up", r"half[- ]?up"),
    ("shipping (4.95)", r"\b4\.95\b"),
    ("free shipping from 50.00", r"\b50\.00\b"),
]
CHANGELOG_BULLETS = [
    "- Line amounts are rounded to cents (half-even).",
    "- Free shipping from 50.00; shipping is 4.95 below that.",
    "- Bulk discount: 10% off a line of 10 or more units.",
    "- The member discount needs a subtotal of 20.00 or more.",
    "- Receipts list the discounts an order got.",
    "- First release: line amounts, the member discount and tax.",
]


def git(*args):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_OPTIONAL_LOCKS": "0"})
    result = subprocess.run(["git", *args], cwd=HERE, env=env, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    return result.returncode, result.stdout.strip()


def read(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read().replace("\r\n", "\n")


class TheMergeIsFinished(unittest.TestCase):
    def test_the_merge_commit_has_both_tips_as_parents(self):
        code, parents = git("rev-parse", "HEAD^@")
        self.assertEqual(code, 0, "HEAD has no commit")
        self.assertEqual(parents.split(), [MAIN_TIP, FEATURE_TIP],
                         "HEAD must be the merge of main's tip and feature/bulk-discount's tip")
        self.assertFalse(os.path.exists(os.path.join(HERE, ".git", "MERGE_HEAD")), "the merge is still in progress")

    def test_the_branches_are_where_they_should_be(self):
        self.assertEqual(git("symbolic-ref", "HEAD")[1], "refs/heads/main")
        self.assertEqual(git("rev-parse", "refs/heads/feature/bulk-discount")[1], FEATURE_TIP)

    def test_nothing_is_left_unmerged_uncommitted_or_lying_around(self):
        self.assertEqual(git("ls-files", "-u")[1], "", "conflicts are still unmerged")
        _code, status = git("status", "--porcelain", "--untracked-files=all")
        left = [line for line in status.splitlines() if line[3:].strip('"') not in MY_FILES]
        self.assertEqual(left, [], "the working tree is not clean")

    def test_the_merge_holds_exactly_the_files_of_both_branches(self):
        # task.md: keep both sides, no leftover files, no file neither branch has.
        def files(rev):
            code, out = git("ls-tree", "-r", "--name-only", rev)
            self.assertEqual(code, 0, f"cannot list {rev}")
            return set(out.splitlines())

        both = files(MAIN_TIP) | files(FEATURE_TIP)
        merged = files("HEAD")
        self.assertEqual((sorted(both - merged), sorted(merged - both)), ([], []),
                         "the merge commit must hold exactly the files of the two branches "
                         "(missing, extra)")

    def test_no_conflict_markers_in_any_tracked_file(self):
        _code, files = git("ls-files")
        for name in files.splitlines():
            path = os.path.join(HERE, name)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh, start=1):
                    if line.startswith(("<<<<<<<", ">>>>>>>", "|||||||")) or line.rstrip("\r\n") == "=======":
                        self.fail(f"{name}:{n} still has a conflict marker")


class BothSidesSurvived(unittest.TestCase):
    def setUp(self):
        import pricing

        self.p = pricing

    def assertCents(self, value, expected):
        self.assertEqual(value, Decimal(expected))
        self.assertEqual(value.as_tuple().exponent, -2, f"{value} is not whole cents")

    def test_line_amounts_get_the_bulk_discount_then_rounding(self):
        line = self.p.line_amount
        self.assertCents(line("2.50", 4), "10.00")
        self.assertCents(line("0.125", 1), "0.12")
        self.assertCents(line("0.135", 1), "0.14")
        self.assertCents(line("2.00", 10), "18.00")
        self.assertCents(line("2.00", 9), "18.00")
        # The order matters: the per-line adjustment first, rounding last.
        self.assertCents(line("0.1116", 10), "1.00")
        self.assertCents(line("1.115", 10), "10.04")
        self.assertCents(line("0.0555", 20), "1.00")

    def test_the_member_minimum_counts_the_discounted_lines(self):
        sub = self.p.subtotal
        self.assertEqual(sub([("1.00", 12), ("10.00", 1)], member=True), Decimal("19.76"))
        self.assertEqual(sub([("0.95", 20)], member=True), Decimal("17.10"))
        self.assertEqual(sub([("5.00", 3)], member=True), Decimal("15.00"))
        self.assertEqual(sub([("10.00", 2)], member=True), Decimal("19.00"))
        # The list price reaches 20.00, the lines' amounts (after the bulk
        # discount) do not: no member discount.
        self.assertEqual(sub([("2.00", 10), ("1.00", 1)], member=True), Decimal("19.00"))
        self.assertEqual(sub([("1.00", 21)], member=True), Decimal("18.90"))
        self.assertEqual(self.p.order_total([("1.00", 21)], member=True), Decimal("25.36"))
        self.assertEqual(self.p.discounts([("1.00", 21)], member=True), ["bulk: line 1"])

    def test_order_totals_follow_every_rule(self):
        total = self.p.order_total
        self.assertEqual(total([("1.00", 12), ("10.00", 1)], member=True), Decimal("26.29"))
        self.assertEqual(total([("3.00", 20)]), Decimal("58.32"))
        self.assertEqual(total([("3.00", 20)], member=True), Decimal("55.40"))
        self.assertEqual(total([("26.00", 2)], member=True), Decimal("58.30"))
        self.assertEqual(total([("10.00", 2)]), Decimal("26.55"))
        self.assertEqual(self.p.shipping(Decimal("49.99")), Decimal("4.95"))
        self.assertEqual(self.p.shipping(Decimal("50.00")), Decimal("0.00"))

    def test_discounts_and_receipts(self):
        self.assertEqual(self.p.discounts([("1.00", 12), ("10.00", 1)], member=True), ["bulk: line 1", "member"])
        self.assertEqual(self.p.discounts([("0.95", 20)], member=True), ["bulk: line 1"])
        self.assertEqual(self.p.discounts([("1.00", 1), ("1.00", 12), ("10.00", 2)], member=True),
                         ["bulk: line 2", "member"])
        import receipts

        self.assertEqual(
            receipts.receipt([("1.00", 12), ("10.00", 2)], member=True),
            "12 x 1.00\n2 x 10.00\ndiscount: bulk: line 1\ndiscount: member\ntotal: 36.55",
        )

    def test_every_test_of_both_sides_is_kept_and_passes(self):
        tree = ast.parse(read("test_pricing.py"))
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        self.assertEqual(sorted(BOTH_SIDES_TESTS - names), [], "tests from one side were dropped")
        calls = []  # [function, args, kwargs, result, values it was compared with]
        pricing_name = self.p.__name__

        def watched(name, func):
            def call(*args, **kwargs):
                result = func(*args, **kwargs)
                # Only the TESTS' own calls count (subtotal calling
                # line_amount inside pricing.py is not a test case).
                if sys._getframe(1).f_globals.get("__name__") != pricing_name:
                    calls.append([name, args, kwargs, result, []])
                return result
            return call

        def comparing(original):
            def compare(case, first, second, *args, **kwargs):
                for entry in calls:
                    if entry[3] is first:
                        entry[4].append(second)
                    elif entry[3] is second:
                        entry[4].append(first)
                return original(case, first, second, *args, **kwargs)
            return compare

        compare_names = ("assertEqual", "assertListEqual", "assertSequenceEqual", "assertTupleEqual")
        originals = {name: getattr(unittest.TestCase, name) for name in compare_names}
        functions = {name: getattr(self.p, name) for name in PRICING_FUNCS if hasattr(self.p, name)}
        try:
            # Watched BEFORE the tests import them, so a name the tests bind
            # (an alias, a table of functions, ``pricing.<name>``) is watched.
            for name, func in functions.items():
                setattr(self.p, name, watched(name, func))
            for name, original in originals.items():
                setattr(unittest.TestCase, name, comparing(original))
            sys.modules.pop("test_pricing", None)
            module = importlib.import_module("test_pricing")
            suite = unittest.defaultTestLoader.loadTestsFromModule(module)
            with open(os.devnull, "w") as sink:
                result = unittest.TextTestRunner(stream=sink, verbosity=0).run(suite)
        finally:
            for name, original in originals.items():
                setattr(unittest.TestCase, name, original)
            for name, func in functions.items():
                setattr(self.p, name, func)
        self.assertTrue(result.wasSuccessful(), "the merged test suite does not pass")
        self.assertGreaterEqual(result.testsRun, len(BOTH_SIDES_TESTS))
        lost = [
            f"{name}{args!r} {kwargs or ''} == {want!r}"
            for name, args, kwargs, want in BOTH_SIDES_CASES
            if not any(e[0] == name and e[1] == args and e[2] == kwargs and want in e[4] for e in calls)
        ]
        self.assertEqual(lost, [], "cases of the two branches' tests that the merged tests no longer check")

    def test_the_readme_states_both_sides_rules(self):
        readme = " ".join(read("README.md").split())
        missing = [what for what, pattern in README_FACTS if not re.search(pattern, readme, re.I)]
        self.assertEqual(missing, [], "rules of the two branches the merged README no longer states")

    def test_the_changelog_keeps_every_entry_once(self):
        log = read("CHANGELOG.md")
        lines = [line.strip() for line in log.splitlines()]
        self.assertEqual(lines.count("## Unreleased"), 1, "one Unreleased section")
        self.assertEqual(lines.count("## 1.0.0"), 1)
        for bullet in CHANGELOG_BULLETS:
            self.assertEqual(lines.count(bullet), 1, bullet)
        self.assertLess(lines.index("## Unreleased"), lines.index("## 1.0.0"))


def main():
    if shutil.which("git") is None:
        print("git is not installed, so this check cannot run")
        return 3
    suite = unittest.TestSuite()
    for case in (TheMergeIsFinished, BothSidesSurvived):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
