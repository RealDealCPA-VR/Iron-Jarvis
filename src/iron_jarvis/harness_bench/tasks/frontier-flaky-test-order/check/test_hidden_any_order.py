# IJ-BENCH-HIDDEN-CHECK frontier-flaky-test-order
"""Every order of the visible suite must pass, and each root cause is probed
on its own. Every run happens in a FRESH Python process (module state is the
whole point), started from this folder."""
import hashlib
import json
import os
import random
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

CRLF = bytes([13, 10])
LF = bytes([10])
UNCHANGED = {
    "test_pricing.py": "d38ec02f15518fab00c1b50c15d867aa61cea5c8be772220605aa6f60ce2d9c9",
    "test_sales.py": "a9192acdd74896d5e4d9986aa48980800e38de03112c933fc555a609e48a5fea",
    "test_stock.py": "129bd49de0c3b92210046e26547980453fcde0bf887460b02970d3f7fc736107",
    "test_units.py": "dbc1efe185c73fafbe16cee46fcc1ba76e5c304265074dfcbe980b287f11266a",
    "run_shuffled.py": "dbc468c735c6bf86ae952ff107bc69cc9a5d2bb9ee7311cf0985b00a664b6ac1",
}

P = "test_pricing.PricingTests."
S = "test_sales.SalesTests."
K = "test_stock.StockTests."
U = "test_units.UnitTests."
TEST_IDS = sorted([
    P + "test_line_total_rounds_half_up",
    P + "test_markup_change_applies",
    P + "test_negative_markup_is_refused",
    P + "test_set_price_refuses_bad_values",
    P + "test_unit_price_adds_the_markup",
    P + "test_unit_price_follows_a_new_base_price",
    P + "test_unit_price_rounds_half_up",
    P + "test_unknown_sku_raises",
    S + "test_duplicate_lines_are_merged",
    S + "test_order_ships_from_stock",
    S + "test_order_short_ships_nothing",
    S + "test_order_total",
    S + "test_other_units_are_converted",
    S + "test_price_list_has_every_product",
    S + "test_price_list_shows_selling_prices",
    K + "test_receive_adds_up",
    K + "test_receive_converts_to_the_product_unit",
    K + "test_reorder_list",
    K + "test_stock_report_lists_what_is_held",
    K + "test_stock_value",
    K + "test_take_reduces_the_level",
    K + "test_taking_more_than_is_held_raises",
    U + "test_case_does_not_matter",
    U + "test_convert_across_dimensions_raises",
    U + "test_convert_between_units",
    U + "test_notes_empty_when_read_as_written",
    U + "test_notes_name_the_alias",
    U + "test_parse_decimal_with_an_alias",
    U + "test_parse_pieces_as_each",
    U + "test_unit_names",
    U + "test_unknown_unit_and_bad_text_raise",
])

RUN_ORDER = r"""
import io, json, os, sys, unittest
sys.path.insert(0, os.getcwd())
order = json.loads(sys.stdin.read())
loader = unittest.TestLoader()
suite = unittest.TestSuite(loader.loadTestsFromName(name) for name in order)
buf = io.StringIO()
result = unittest.TextTestRunner(stream=buf, verbosity=0).run(suite)
bad = [t.id() for t, _ in result.failures + result.errors]
print(json.dumps({"ran": result.testsRun, "failed": bad}))
sys.stderr.write(buf.getvalue()[-1500:])
sys.exit(0 if result.wasSuccessful() else 1)
"""


def _python(code, stdin=""):
    return subprocess.run(
        [sys.executable, "-I", "-B", "-c", code], cwd=HERE, input=stdin,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
    )


def _move_first(order, *names):
    picked = [n for n in names]
    return picked + [n for n in order if n not in picked]


def _orders():
    orders = {"the normal order": list(TEST_IDS), "the reverse order": list(reversed(TEST_IDS))}
    orders["an alias read first"] = _move_first(TEST_IDS, U + "test_parse_pieces_as_each")
    orders["an order with washers priced first"] = _move_first(
        TEST_IDS, S + "test_duplicate_lines_are_merged", P + "test_unit_price_follows_a_new_base_price")
    orders["the stock report first"] = _move_first(
        TEST_IDS, K + "test_stock_report_lists_what_is_held", P + "test_markup_change_applies")
    orders["the price list first"] = _move_first(TEST_IDS, S + "test_price_list_has_every_product")
    rng = random.Random(20261024)
    for k in range(40):
        order = list(TEST_IDS)
        rng.shuffle(order)
        orders[f"shuffled order #{k + 1}"] = order
    return orders


class TheTestsAreUnchanged(unittest.TestCase):
    def test_the_tests_and_the_runner_are_unchanged(self):
        for name, digest in UNCHANGED.items():
            with self.subTest(name=name):
                with open(os.path.join(HERE, name), "rb") as fh:
                    data = fh.read().replace(CRLF, LF)
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest, f"{name} was changed")


class AnyOrderPasses(unittest.TestCase):
    def test_every_order_passes_in_a_fresh_process(self):
        failing = []
        for label, order in _orders().items():
            done = _python(RUN_ORDER, json.dumps(order))
            try:
                verdict = json.loads(done.stdout.strip().splitlines()[-1])
            except (IndexError, ValueError):
                failing.append(f"{label}: the run did not finish ({done.stderr[-600:]})")
                continue
            if done.returncode != 0 or verdict["ran"] != len(TEST_IDS):
                short = [t.rsplit(".", 1)[-1] for t in verdict["failed"]]
                failing.append(f"{label}: {verdict['ran']} ran, failed {short}")
        if failing:
            self.fail(f"{len(failing)} of {len(_orders())} orders failed; the first: " + " | ".join(failing[:3]))


PROBE_NOTES = r"""
import os, sys
sys.path.insert(0, os.getcwd())
from inventory import units
units.parse_quantity("4 pcs")
units.parse_quantity("1 dz")
units.convert(24, "pcs", "dozen")
assert units.parse_quantity("2 kg").notes == (), units.parse_quantity("2 kg").notes
q = units.parse_quantity("3 ea")
assert q.notes == ("read 'ea' as 'each'",), q.notes
assert units.parse_quantity("5 m").notes == ()
again = units.parse_quantity("4 pcs")
assert again.notes == ("read 'pcs' as 'each'",), again.notes
print("ok")
"""

PROBE_BASE_PRICE = r"""
import os, sys
sys.path.insert(0, os.getcwd())
from inventory import pricing, report
from inventory.stock import Stockroom
before = report.price_list()
assert pricing.unit_price("WASHER-M6") == 5
room = Stockroom()
room.receive("WASHER-M6", "1 box")
assert room.value_cents() == 500, room.value_cents()
pricing.set_price("WASHER-M6", 8)
assert pricing.unit_price("WASHER-M6") == 10, pricing.unit_price("WASHER-M6")
assert pricing.line_total("WASHER-M6", 3) == 30
assert room.value_cents() == 1000, room.value_cents()
rows = {line.split()[0]: line for line in report.price_list().splitlines()[1:]}
assert rows["WASHER-M6"].endswith("  0.10"), rows["WASHER-M6"]
pricing.set_price("WASHER-M6", 4)
assert pricing.unit_price("WASHER-M6") == 5
assert report.price_list() == before
print("ok")
"""

PROBE_MARKUP = r"""
import os, sys
from fractions import Fraction
sys.path.insert(0, os.getcwd())
from inventory import catalog, pricing
normal = {sku: pricing.unit_price(sku) for sku in catalog.skus()}
assert normal == {"BOLT-M6": 15, "CHAIN-5": 1188, "GREASE": 1750, "HINGE-75": 225, "WASHER-M6": 5}, normal
pricing.set_markup(Fraction(1, 2))
half = {sku: pricing.unit_price(sku) for sku in catalog.skus()}
assert half == {"BOLT-M6": 18, "CHAIN-5": 1425, "GREASE": 2100, "HINGE-75": 270, "WASHER-M6": 6}, half
pricing.set_markup("0.25")
back = {sku: pricing.unit_price(sku) for sku in catalog.skus()}
assert back == normal, back
print("ok")
"""


class EachCauseOnItsOwn(unittest.TestCase):
    def _probe(self, code, what):
        done = _python(code)
        self.assertEqual(done.returncode, 0, f"{what}: {done.stderr[-1200:]}")

    def test_notes_belong_to_one_reading(self):
        self._probe(PROBE_NOTES, "the notes of one reading included notes of earlier readings")

    def test_a_new_base_price_is_used_at_once(self):
        self._probe(PROBE_BASE_PRICE, "a price read before set_price stayed in use after it")

    def test_a_new_markup_is_used_at_once(self):
        self._probe(PROBE_MARKUP, "prices read before set_markup stayed in use after it")


if __name__ == "__main__":
    unittest.main()
