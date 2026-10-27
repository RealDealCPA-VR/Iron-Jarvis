import unittest

from orderdesk.pricing import line_total, money, subtotal


class PricingTest(unittest.TestCase):
    def test_line_total(self):
        self.assertEqual(line_total(1250, 2), 2500)
        with self.assertRaises(ValueError):
            line_total(1250, 0)

    def test_subtotal(self):
        lines = [{"price_cents": 1250, "qty": 2}, {"price_cents": 4500, "qty": 1}]
        self.assertEqual(subtotal(lines), 7000)

    def test_money(self):
        self.assertEqual(money(123456), "$1,234.56")
        self.assertEqual(money(-5), "-$0.05")


if __name__ == "__main__":
    unittest.main()
