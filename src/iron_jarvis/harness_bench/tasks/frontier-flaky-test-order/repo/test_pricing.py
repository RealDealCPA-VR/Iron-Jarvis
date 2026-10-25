import unittest
from fractions import Fraction

from inventory import catalog, pricing


class PricingTests(unittest.TestCase):
    def test_line_total_rounds_half_up(self):
        # 1/8 m of chain at 11.88 a metre is 148.5 cents.
        self.assertEqual(pricing.line_total("CHAIN-5", Fraction(1, 8)), 149)

    def test_markup_change_applies(self):
        old = pricing.get_markup()
        pricing.set_markup(Fraction(1, 2))
        try:
            self.assertEqual(pricing.unit_price("HINGE-75"), 270)
        finally:
            pricing.set_markup(old)

    def test_negative_markup_is_refused(self):
        with self.assertRaises(ValueError):
            pricing.set_markup(-1)
        self.assertEqual(pricing.get_markup(), pricing.DEFAULT_MARKUP)

    def test_unit_price_follows_a_new_base_price(self):
        pricing.set_price("WASHER-M6", 8)
        try:
            self.assertEqual(pricing.unit_price("WASHER-M6"), 10)
            self.assertEqual(pricing.line_total("WASHER-M6", 3), 30)
        finally:
            pricing.set_price("WASHER-M6", 4)

    def test_set_price_refuses_bad_values(self):
        for bad in (-5, 1.5, True, "12"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    pricing.set_price("BOLT-M6", bad)
        self.assertEqual(catalog.get("BOLT-M6").base_cents, 12)

    def test_unit_price_adds_the_markup(self):
        self.assertEqual(pricing.unit_price("BOLT-M6"), 15)
        self.assertEqual(pricing.unit_price("GREASE"), 1750)

    def test_unit_price_rounds_half_up(self):
        self.assertEqual(pricing.unit_price("CHAIN-5"), 1188)

    def test_unknown_sku_raises(self):
        with self.assertRaises(catalog.UnknownProduct):
            pricing.unit_price("NOPE-1")


if __name__ == "__main__":
    unittest.main()
