import unittest

from money.currency import format_money, parse_money


class CurrencyTest(unittest.TestCase):
    def test_format(self):
        self.assertEqual(format_money(123456), "$1,234.56")
        self.assertEqual(format_money(-5), "-$0.05")
        self.assertEqual(format_money(700, symbol="EUR "), "EUR 7.00")

    def test_parse_reads_what_format_writes(self):
        for cents in (0, 5, 99, 100, 123456, -98765):
            self.assertEqual(parse_money(format_money(cents)), cents)


if __name__ == "__main__":
    unittest.main()
