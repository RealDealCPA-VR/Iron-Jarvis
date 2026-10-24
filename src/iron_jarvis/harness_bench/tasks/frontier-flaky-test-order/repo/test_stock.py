import unittest
from fractions import Fraction

from inventory import report
from inventory.stock import OutOfStock, Stockroom


class StockTests(unittest.TestCase):
    def test_receive_converts_to_the_product_unit(self):
        room = Stockroom()
        self.assertEqual(room.receive("GREASE", "500 g"), Fraction(1, 2))
        self.assertEqual(room.receive("BOLT-M6", "2 dozen"), 24)
        self.assertEqual(room.level("GREASE"), Fraction(1, 2))

    def test_receive_adds_up(self):
        room = Stockroom()
        room.receive("CHAIN-5", "150 cm")
        room.receive("CHAIN-5", "2 m")
        self.assertEqual(room.level("CHAIN-5"), Fraction(7, 2))

    def test_reorder_list(self):
        room = Stockroom()
        room.receive("BOLT-M6", "250 each")
        room.receive("HINGE-75", "5 each")
        self.assertEqual(
            room.reorder_list(),
            [("CHAIN-5", 20), ("GREASE", 10), ("HINGE-75", 35), ("WASHER-M6", 1000)],
        )

    def test_stock_report_lists_what_is_held(self):
        room = Stockroom()
        room.receive("HINGE-75", "5 each")
        room.receive("WASHER-M6", "1 box")
        lines = report.stock_report(room).splitlines()
        self.assertEqual(lines[0].split(), ["SKU", "Level", "Unit", "Value"])
        self.assertEqual([line.split()[:3] for line in lines[1:-1]],
                         [["HINGE-75", "5", "each"], ["WASHER-M6", "100", "each"]])
        self.assertTrue(lines[-1].startswith("Total value: "))

    def test_stock_value(self):
        room = Stockroom()
        room.receive("BOLT-M6", "100 each")
        room.receive("GREASE", "2 kg")
        self.assertEqual(room.value_cents(), 1500 + 3500)

    def test_take_reduces_the_level(self):
        room = Stockroom()
        room.receive("BOLT-M6", "1 box")
        room.take("BOLT-M6", "3 dozen")
        self.assertEqual(room.level("BOLT-M6"), 64)

    def test_taking_more_than_is_held_raises(self):
        room = Stockroom()
        room.receive("GREASE", "1 kg")
        with self.assertRaises(OutOfStock):
            room.take("GREASE", "1500 g")
        self.assertEqual(room.level("GREASE"), 1)


if __name__ == "__main__":
    unittest.main()
