import unittest
from decimal import Decimal

from shipping import settings, units


class UnitsTests(unittest.TestCase):
    def setUp(self):
        settings.reset()
        self.addCleanup(settings.reset)

    def test_metric(self):
        self.assertEqual(units.weight_unit(), "kg")
        self.assertEqual(units.weight_value(Decimal("2.5")), Decimal("2.50"))
        self.assertEqual(units.format_weight(Decimal("0.4536")), "0.45 kg")
        self.assertEqual(units.format_dims((30, Decimal("20.5"), 10)), "30.0 x 20.5 x 10.0 cm")

    def test_imperial(self):
        settings.CONFIG["units"] = "imperial"
        self.assertEqual(units.weight_unit(), "lb")
        self.assertEqual(units.weight_value(Decimal("2.5")), Decimal("5.51"))
        self.assertEqual(units.format_weight(Decimal("0.4536")), "1.00 lb")
        self.assertEqual(units.format_dims((30, 20, 10)), "11.8 x 7.9 x 3.9 in")

    def test_parse_weight(self):
        self.assertEqual(units.parse_weight("2.5 kg"), Decimal("2.5"))
        self.assertEqual(units.parse_weight(" 5 LB "), Decimal("2.26796185"))
        self.assertEqual(units.parse_weight("3"), Decimal("3"))
        self.assertEqual(units.parse_weight(4), Decimal("4"))
        for bad in ("abc", "2.5 stone", "0 kg"):
            with self.subTest(bad=bad):
                with self.assertRaises(units.UnitError):
                    units.parse_weight(bad)

    def test_a_bare_number_is_in_the_configured_unit(self):
        settings.CONFIG["units"] = "imperial"
        self.assertEqual(units.parse_weight("3"), Decimal("1.36077711"))
        self.assertEqual(units.parse_weight("3 kg"), Decimal("3"))


if __name__ == "__main__":
    unittest.main()
