import unittest

from inventory.orders import cancel_order, place_order
from inventory.stock import Stock


class OrdersTest(unittest.TestCase):
    def setUp(self):
        self.stock = Stock()
        self.stock.receive("AB-1", 5)
        self.stock.receive("CD-2", 2)

    def test_an_order_is_all_or_none(self):
        with self.assertRaises(ValueError):
            place_order(self.stock, [("AB-1", 2), ("CD-2", 3)])
        self.assertEqual(self.stock.available("AB-1"), 5)

    def test_cancel_gives_everything_back(self):
        lines = [("ab-1", 2), ("cd-2", 1)]
        place_order(self.stock, lines)
        cancel_order(self.stock, lines)
        self.assertEqual((self.stock.available("AB-1"), self.stock.available("CD-2")), (5, 2))


if __name__ == "__main__":
    unittest.main()
