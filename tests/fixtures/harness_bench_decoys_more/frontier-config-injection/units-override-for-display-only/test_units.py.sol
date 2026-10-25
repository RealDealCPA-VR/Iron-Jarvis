import unittest
from decimal import Decimal

from shipping import units
from shipping.settings import Settings

METRIC = Settings()
IMPERIAL = Settings(units="imperial")


class UnitsTests(unittest.TestCase):
    def test_metric(self):
        self.assertEqual(units.weight_unit(settings=METRIC), "kg")
        self.assertEqual(units.weight_value(Decimal("2.5"), settings=METRIC), Decimal("2.50"))
        self.assertEqual(units.format_weight(Decimal("0.4536"), settings=METRIC), "0.45 kg")
        self.assertEqual(units.format_dims((30, Decimal("20.5"), 10), settings=METRIC), "30.0 x 20.5 x 10.0 cm")

    def test_imperial(self):
        self.assertEqual(units.weight_unit(settings=IMPERIAL), "lb")
        self.assertEqual(units.weight_value(Decimal("2.5"), settings=IMPERIAL), Decimal("5.51"))
        self.assertEqual(units.format_weight(Decimal("0.4536"), settings=IMPERIAL), "1.00 lb")
        self.assertEqual(units.format_dims((30, 20, 10), settings=IMPERIAL), "11.8 x 7.9 x 3.9 in")

    def test_parse_weight(self):
        self.assertEqual(units.parse_weight("2.5 kg", settings=METRIC), Decimal("2.5"))
        self.assertEqual(units.parse_weight(" 5 LB ", settings=METRIC), Decimal("2.26796185"))
        self.assertEqual(units.parse_weight("3", settings=METRIC), Decimal("3"))
        self.assertEqual(units.parse_weight(4, settings=METRIC), Decimal("4"))
        for bad in ("abc", "2.5 stone", "0 kg"):
            with self.subTest(bad=bad):
                with self.assertRaises(units.UnitError):
                    units.parse_weight(bad, settings=METRIC)

    def test_a_bare_number_is_in_the_configured_unit(self):
        self.assertEqual(units.parse_weight("3", settings=IMPERIAL), Decimal("1.36077711"))
        self.assertEqual(units.parse_weight("3 kg", settings=IMPERIAL), Decimal("3"))
        self.assertEqual(units.parse_weight("3", settings=METRIC), Decimal("3"))


if __name__ == "__main__":
    unittest.main()
