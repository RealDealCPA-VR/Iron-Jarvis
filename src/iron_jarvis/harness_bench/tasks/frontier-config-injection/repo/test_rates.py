import unittest
from decimal import Decimal

from shipping import rates, settings


class RatesTests(unittest.TestCase):
    def setUp(self):
        settings.reset()
        self.addCleanup(settings.reset)

    def test_bands(self):
        self.assertEqual(rates.band(1), 0)
        self.assertEqual(rates.band(Decimal("1.01")), 1)
        self.assertEqual(rates.band(70), 5)
        with self.assertRaises(rates.RateError):
            rates.band(Decimal("70.01"))

    def test_base_price(self):
        self.assertEqual(rates.base_price(3, 4), Decimal("9.90"))
        with self.assertRaises(rates.RateError):
            rates.base_price(9, 1)

    def test_price_in_dollars_and_euros(self):
        self.assertEqual(rates.price(8, Decimal("12")), Decimal("30.80"))
        settings.CONFIG.update(currency="EUR", fuel_surcharge_pct=10)
        self.assertEqual(rates.price(8, Decimal("12")), Decimal("31.17"))
        settings.CONFIG.update(currency="GBP", fuel_surcharge_pct=0)
        self.assertEqual(rates.price(1, Decimal("0.5")), Decimal("4.11"))

    def test_format_price(self):
        self.assertEqual(rates.format_price(Decimal("12.3")), "USD 12.30")
        settings.CONFIG["currency"] = "EUR"
        self.assertEqual(rates.format_price(7), "EUR 7.00")


if __name__ == "__main__":
    unittest.main()
