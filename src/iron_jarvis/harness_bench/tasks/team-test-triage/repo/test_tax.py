import unittest
from decimal import Decimal

from money.tax import line_tax


class LineTaxTest(unittest.TestCase):
    def test_rounds_to_the_nearest_cent(self):
        self.assertEqual(line_tax(115, "7.25"), 8)
        self.assertEqual(line_tax(10, 10), 1)

    def test_half_a_cent_rounds_up(self):
        self.assertEqual(line_tax(250, 5), 13)

    def test_another_half_cent(self):
        self.assertEqual(line_tax(1050, 5), 53)

    def test_a_decimal_rate(self):
        self.assertEqual(line_tax(1000, Decimal("8.25")), 83)

    def test_nothing_to_tax(self):
        self.assertEqual(line_tax(0, 5), 0)


if __name__ == "__main__":
    unittest.main()
