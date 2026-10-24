import unittest
from fractions import Fraction

from inventory import catalog, report
from inventory.sales import Order
from inventory.stock import OutOfStock, Stockroom


class SalesTests(unittest.TestCase):
    def test_duplicate_lines_are_merged(self):
        order = Order("acme")
        order.add("WASHER-M6", "100 each")
        order.add("BOLT-M6", "4 each")
        order.add("WASHER-M6", "1 box")
        self.assertEqual(
            [(line.sku, line.quantity) for line in order.lines()],
            [("WASHER-M6", 200), ("BOLT-M6", 4)],
        )

    def test_order_total(self):
        order = Order("acme")
        order.add("BOLT-M6", "10 each")
        order.add("CHAIN-5", "2 m")
        self.assertEqual(order.total_cents(), 150 + 2376)

    def test_other_units_are_converted(self):
        order = Order("acme")
        order.add("GREASE", "500 g")
        order.add("CHAIN-5", "150 cm")
        lines = order.lines()
        self.assertEqual([line.quantity for line in lines], [Fraction(1, 2), Fraction(3, 2)])
        self.assertEqual([line.total_cents for line in lines], [875, 1782])

    def test_price_list_has_every_product(self):
        lines = report.price_list().splitlines()
        self.assertEqual(lines[0].split(), ["SKU", "Product", "Unit", "Price"])
        self.assertEqual([line.split()[0] for line in lines[1:]], catalog.skus())

    def test_price_list_shows_selling_prices(self):
        rows = {line.split()[0]: line for line in report.price_list().splitlines()[1:]}
        self.assertTrue(rows["BOLT-M6"].endswith("  0.15"), rows["BOLT-M6"])
        self.assertTrue(rows["GREASE"].endswith("  17.50"), rows["GREASE"])

    def test_order_ships_from_stock(self):
        room = Stockroom()
        room.receive("BOLT-M6", "1 box")
        order = Order("acme")
        order.add("BOLT-M6", "30 each")
        order.ship_from(room)
        self.assertEqual(room.level("BOLT-M6"), 70)

    def test_order_short_ships_nothing(self):
        room = Stockroom()
        room.receive("BOLT-M6", "10 each")
        room.receive("GREASE", "1 kg")
        order = Order("acme")
        order.add("BOLT-M6", "5 each")
        order.add("GREASE", "2 kg")
        with self.assertRaises(OutOfStock):
            order.ship_from(room)
        self.assertEqual((room.level("BOLT-M6"), room.level("GREASE")), (10, 1))


if __name__ == "__main__":
    unittest.main()
