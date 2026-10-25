import unittest
from decimal import Decimal

from ledger.money import Money


class MoneyTests(unittest.TestCase):
    def test_parse_reads_amounts_as_written_in_a_file(self):
        self.assertEqual(Money.parse("12.50").amount, Decimal("12.50"))
        self.assertEqual(Money.parse(" -3 ").amount, Decimal("-3"))
        self.assertEqual(Money.parse("0.5").amount, Decimal("0.5"))

    def test_parse_refuses_anything_else(self):
        for text in ("1.234", "abc", "", "1,000.00", "+5", "1e3", "12."):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    Money.parse(text)

    def test_floats_are_refused(self):
        with self.assertRaises(TypeError):
            Money(1.5)

    def test_arithmetic_is_exact(self):
        self.assertEqual(Money("0.10") + Money("0.20"), Money("0.30"))
        self.assertEqual(Money("5") - Money("7.25"), Money("-2.25"))
        self.assertEqual(-Money("4.00"), Money("-4"))

    def test_equality_and_order(self):
        self.assertEqual(Money("1.5"), Money("1.50"))
        self.assertEqual(hash(Money("1.5")), hash(Money("1.50")))
        self.assertLess(Money("1"), Money("2"))
        self.assertGreaterEqual(Money("2"), Money("2.00"))

    def test_format(self):
        self.assertEqual(Money("1234.5").format(), "1,234.50")
        self.assertEqual(Money("-12").format(), "-12.00")
        self.assertEqual(Money("1234567.891").format(), "1,234,567.89")
        self.assertEqual(Money("0.125").format(), "0.12")
        self.assertEqual(Money("0.135").format(), "0.14")
        self.assertEqual(Money("-0.001").format(), "0.00")
        self.assertEqual(str(Money("7")), "7.00")


if __name__ == "__main__":
    unittest.main()
