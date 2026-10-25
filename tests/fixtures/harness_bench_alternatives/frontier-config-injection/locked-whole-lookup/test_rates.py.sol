import unittest
from decimal import Decimal

from shipping import rates
from shipping.settings import Settings


class RatesTests(unittest.TestCase):
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
        self.assertEqual(rates.price(8, Decimal("12"), settings=Settings()), Decimal("30.80"))
        euros = Settings(currency="EUR", fuel_surcharge_pct=10)
        self.assertEqual(rates.price(8, Decimal("12"), settings=euros), Decimal("31.17"))
        self.assertEqual(rates.price(1, Decimal("0.5"), settings=Settings(currency="GBP")), Decimal("4.11"))

    def test_format_price(self):
        self.assertEqual(rates.format_price(Decimal("12.3"), settings=Settings()), "USD 12.30")
        self.assertEqual(rates.format_price(7, settings=Settings(currency="EUR")), "EUR 7.00")


if __name__ == "__main__":
    unittest.main()
