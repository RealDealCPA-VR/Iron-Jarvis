import unittest
from decimal import Decimal

from shipping import packaging
from shipping.model import make_item
from shipping.settings import Settings

BOOK = make_item("book", (24, 17, 3), "0.9")
MUG = make_item("mug", (12, 9, 7), "0.4")
KETTLEBELL = make_item("kettlebell", (20, 20, 20), "14.5")
DEFAULT = Settings()


class PackagingTests(unittest.TestCase):
    def test_the_smallest_box_that_fits(self):
        self.assertEqual(packaging.needed_space([BOOK, MUG], settings=DEFAULT), (Decimal(28), Decimal(21), Decimal(14)))
        self.assertEqual(packaging.choose_box([BOOK, MUG], settings=DEFAULT).name, "M")
        self.assertEqual(packaging.total_weight([BOOK, MUG]), Decimal("1.3"))
        box = packaging.choose_box([BOOK, MUG], settings=DEFAULT)
        self.assertEqual(packaging.total_weight([BOOK, MUG], box), Decimal("1.70"))

    def test_padding_decides_the_box(self):
        card = make_item("card", (18, 13, 3), "0.1")
        self.assertEqual(packaging.choose_box([card], settings=DEFAULT).name, "M")
        self.assertEqual(packaging.choose_box([BOOK, MUG], settings=Settings(padding_cm=4)).name, "L")
        self.assertEqual(packaging.choose_box([card], settings=Settings(padding_cm=0)).name, "S")

    def test_too_heavy(self):
        self.assertEqual(packaging.choose_box([KETTLEBELL], settings=DEFAULT).name, "L")
        with self.assertRaises(packaging.PackagingError) as caught:
            packaging.choose_box([KETTLEBELL], settings=Settings(max_box_kg=15))
        self.assertEqual(str(caught.exception), "too heavy for one box: 15.30 kg (the limit is 15.00 kg)")

    def test_too_big(self):
        with self.assertRaises(packaging.PackagingError) as caught:
            packaging.choose_box([make_item("ski", (180, 10, 10), "3")], settings=DEFAULT)
        self.assertEqual(str(caught.exception), "no box is big enough for these items")


if __name__ == "__main__":
    unittest.main()
