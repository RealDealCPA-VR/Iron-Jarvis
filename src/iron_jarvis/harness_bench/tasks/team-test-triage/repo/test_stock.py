import unittest

from inventory.stock import Stock


class StockTest(unittest.TestCase):
    def setUp(self):
        self.stock = Stock()
        self.stock.receive("AB-1", 5)

    def test_reserve_and_ship(self):
        self.stock.reserve("AB-1", 2)
        self.stock.ship("AB-1", 2)
        self.assertEqual(self.stock.on_hand("AB-1"), 3)
        self.assertEqual(self.stock.available("AB-1"), 3)

    def test_release_gives_the_units_back(self):
        self.stock.reserve("ab-1", 3)
        self.stock.release("ab-1", 3)
        self.assertEqual(self.stock.available("AB-1"), 5)

    def test_a_partial_release(self):
        self.stock.reserve("AB-1", 4)
        self.stock.release("ab-1", 1)
        self.assertEqual(self.stock.reserved("AB-1"), 3)

    def test_reserve_release_reserve(self):
        self.stock.reserve("ab-1", 5)
        self.stock.release("Ab-1", 5)
        self.stock.reserve("AB-1", 5)
        self.assertEqual(self.stock.available("ab-1"), 0)

    def test_releasing_more_than_reserved_is_refused(self):
        self.stock.reserve("AB-1", 1)
        with self.assertRaises(ValueError):
            self.stock.release("AB-1", 2)


if __name__ == "__main__":
    unittest.main()
