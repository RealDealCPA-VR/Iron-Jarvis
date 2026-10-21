# IJ-BENCH-HIDDEN-CHECK refactor-tax-constant
import importlib
import os
import re
import unittest

import config
import pricing
import quote
import receipt

RATE_LITERAL = re.compile(r"(?<![\d.])[01]?\.0825(?!\d)")


class HiddenTax(unittest.TestCase):
    def test_the_constant(self):
        self.assertEqual(config.TAX_RATE, 0.0825)

    def test_results_unchanged(self):
        self.assertEqual(pricing.price_with_tax(19.99), 21.64)
        self.assertEqual(quote.quote_total([12.5, 7.25]), {"net": 19.75, "tax": 1.63, "total": 21.38})
        self.assertEqual(receipt.tax_part(21.64), 1.65)

    def test_number_lives_in_one_place(self):
        here = os.path.dirname(os.path.abspath(__file__))
        found = []
        for name in sorted(os.listdir(here)):
            if not name.endswith(".py") or name == "config.py" or name.startswith("test_"):
                continue
            with open(os.path.join(here, name), encoding="utf-8") as fh:
                if RATE_LITERAL.search(fh.read()):
                    found.append(name)
        self.assertEqual(found, [])

    def test_calculations_follow_the_constant(self):
        saved = config.TAX_RATE
        try:
            config.TAX_RATE = 0.10
            for module in (pricing, quote, receipt):
                importlib.reload(module)
            self.assertEqual(pricing.price_with_tax(100), 110.0)
            self.assertEqual(quote.quote_total([100])["tax"], 10.0)
            self.assertEqual(receipt.tax_part(110), 10.0)
        finally:
            config.TAX_RATE = saved
            for module in (pricing, quote, receipt):
                importlib.reload(module)
