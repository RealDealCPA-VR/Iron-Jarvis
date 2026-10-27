import unittest

from inventory.catalog import Catalog


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog()
        self.catalog.add("ab-1", "Blue cotton shirt", 2500)
        self.catalog.add("CD-2", "Red cotton scarf", 1200)
        self.catalog.add("EF-3", "Blue wool hat", 1800)

    def test_get_by_any_case(self):
        self.assertEqual(self.catalog.get(" AB-1 ")["name"], "Blue cotton shirt")

    def test_search_every_word(self):
        found = [item["sku"] for item in self.catalog.search("blue")]
        self.assertEqual(found, ["AB-1", "EF-3"])
        found = [item["sku"] for item in self.catalog.search("COTTON scarf")]
        self.assertEqual(found, ["CD-2"])

    def test_no_duplicates(self):
        with self.assertRaises(ValueError):
            self.catalog.add("AB-1", "Another shirt", 100)


if __name__ == "__main__":
    unittest.main()
