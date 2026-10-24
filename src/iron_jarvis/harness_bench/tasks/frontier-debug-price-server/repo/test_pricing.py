import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pricing  # noqa: E402


class ComputeTest(unittest.TestCase):
    """The computation itself (README.md: How a price is computed)."""

    def test_regular_customer_in_dollars(self):
        got = pricing._compute_price("A100", "regular", "USD", 1)
        self.assertEqual(
            got, {"sku": "A100", "tier": "regular", "currency": "USD", "qty": 1, "unit": "24.90", "total": "24.90"}
        )

    def test_tier_currency_and_quantity_break(self):
        got = pricing._compute_price("B220", "gold", "EUR", 12)
        self.assertEqual((got["unit"], got["total"]), ("148.63", "1783.56"))

    def test_yen_has_no_decimals(self):
        got = pricing._compute_price("C310", "silver", "JPY", 3)
        self.assertEqual((got["unit"], got["total"]), ("10426", "31278"))

    def test_the_biggest_break(self):
        got = pricing._compute_price("D405", "platinum", "GBP", 250)
        self.assertEqual((got["unit"], got["total"]), ("5.78", "1445.00"))


class NormalizeTest(unittest.TestCase):
    def test_codes_are_case_insensitive(self):
        self.assertEqual(pricing.normalize(" a100 ", "Gold", "eur", "05"), ("A100", "gold", "EUR", 5))

    def test_unknown_codes_are_refused(self):
        for args in (("Z999", "regular", "USD", 1), ("A100", "vip", "USD", 1),
                     ("A100", "regular", "XYZ", 1), ("A100", "regular", "USD", "two")):
            with self.subTest(args=args), self.assertRaises(pricing.PriceError):
                pricing.normalize(*args)


if __name__ == "__main__":
    unittest.main()
