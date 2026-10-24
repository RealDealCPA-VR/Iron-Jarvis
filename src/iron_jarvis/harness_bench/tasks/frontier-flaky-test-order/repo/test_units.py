import unittest
from fractions import Fraction

from inventory import units
from inventory.units import Quantity, UnitError, parse_quantity


class UnitTests(unittest.TestCase):
    def test_case_does_not_matter(self):
        self.assertEqual(parse_quantity("2 KG"), Quantity(Fraction(2), "kg", ()))

    def test_convert_across_dimensions_raises(self):
        with self.assertRaises(UnitError):
            units.convert(1, "kg", "m")

    def test_convert_between_units(self):
        self.assertEqual(units.convert(2, "dozen", "each"), 24)
        self.assertEqual(units.convert(1500, "g", "kg"), Fraction(3, 2))
        self.assertEqual(units.convert(25, "cm", "m"), Fraction(1, 4))

    def test_notes_empty_when_read_as_written(self):
        self.assertEqual(parse_quantity("3 kg").notes, ())
        self.assertEqual(parse_quantity("1.25 m").notes, ())

    def test_notes_name_the_alias(self):
        self.assertEqual(parse_quantity("2 dz"), Quantity(Fraction(2), "dozen", ("read 'dz' as 'dozen'",)))

    def test_parse_decimal_with_an_alias(self):
        quantity = parse_quantity("1.5 kilo")
        self.assertEqual((quantity.value, quantity.unit), (Fraction(3, 2), "kg"))

    def test_parse_pieces_as_each(self):
        quantity = parse_quantity("12 pcs")
        self.assertEqual((quantity.value, quantity.unit), (12, "each"))

    def test_unit_names(self):
        self.assertEqual(units.unit_names(), ["box", "cm", "dozen", "each", "g", "kg", "m", "mm"])

    def test_unknown_unit_and_bad_text_raise(self):
        for text in ("3 parsecs", "kg 3", "", "3"):
            with self.subTest(text=text):
                with self.assertRaises(UnitError):
                    parse_quantity(text)


if __name__ == "__main__":
    unittest.main()
