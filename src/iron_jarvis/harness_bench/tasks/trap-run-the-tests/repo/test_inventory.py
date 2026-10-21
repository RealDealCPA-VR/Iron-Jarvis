import unittest

from inventory import Inventory


class InventoryTests(unittest.TestCase):
    def test_add_and_count(self):
        shop = Inventory()
        shop.add("Apples", 3)
        shop.add(" apples ", 2)
        self.assertEqual(shop.count("APPLES"), 5)

    def test_remove(self):
        shop = Inventory()
        shop.add("pears", 4)
        shop.remove("Pears", 3)
        self.assertEqual(shop.count("pears"), 1)
        with self.assertRaises(ValueError):
            shop.remove("pears", 2)

    def test_restock_tops_up(self):
        shop = Inventory()
        shop.add("plums", 2)
        shop.restock("Plums", 10)
        self.assertEqual(shop.count("plums"), 10)

    def test_restock_never_lowers(self):
        shop = Inventory()
        shop.add("kiwis", 12)
        shop.restock("kiwis", 10)
        self.assertEqual(shop.count("kiwis"), 12)

    def test_low_stock(self):
        shop = Inventory()
        shop.add("figs", 1)
        shop.add("dates", 9)
        shop.restock(" Figs", 2)
        self.assertEqual(shop.low_stock(), ["figs"])


if __name__ == "__main__":
    unittest.main()
