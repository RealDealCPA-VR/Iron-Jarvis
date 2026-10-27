import unittest

from inventory.labels import shelf_label


class ShelfLabelTest(unittest.TestCase):
    def test_a_label(self):
        self.assertEqual(shelf_label("ab-1"), "Blue cotton shirt: $25.00")

    def test_every_label(self):
        self.assertEqual([shelf_label(s) for s in ("CD-2", "EF-3", "GH-4")], [
            "Red cotton scarf: $12.00", "Blue wool hat: $18.00", "Gift card sleeve: $9.99"])


if __name__ == "__main__":
    unittest.main()
