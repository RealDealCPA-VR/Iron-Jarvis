import unittest

from invoice import invoice_lines
from orders import average_line, order_total
from shipping import shipping_fee

ITEMS = [(2.5, 4), (10.0, 1), (0.99, 3)]


class OrderTests(unittest.TestCase):
    def test_total(self):
        self.assertEqual(order_total(ITEMS), 22.97)

    def test_average(self):
        self.assertEqual(average_line(ITEMS), 7.66)
        self.assertEqual(average_line([]), 0.0)

    def test_invoice(self):
        self.assertEqual(invoice_lines("Ada", ITEMS)[-1], "Total: 22.97")

    def test_shipping(self):
        self.assertEqual(shipping_fee(ITEMS), 4.95)
        self.assertEqual(shipping_fee([(25.0, 2)]), 0.0)


if __name__ == "__main__":
    unittest.main()
