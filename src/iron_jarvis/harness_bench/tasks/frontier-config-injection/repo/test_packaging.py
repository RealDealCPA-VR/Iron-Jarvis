import unittest
from decimal import Decimal

from shipping import packaging, settings
from shipping.model import make_item

BOOK = make_item("book", (24, 17, 3), "0.9")
MUG = make_item("mug", (12, 9, 7), "0.4")
KETTLEBELL = make_item("kettlebell", (20, 20, 20), "14.5")


class PackagingTests(unittest.TestCase):
    def setUp(self):
        settings.reset()
        self.addCleanup(settings.reset)

    def test_the_smallest_box_that_fits(self):
        self.assertEqual(packaging.needed_space([BOOK, MUG]), (Decimal(28), Decimal(21), Decimal(14)))
        self.assertEqual(packaging.choose_box([BOOK, MUG]).name, "M")
        self.assertEqual(packaging.total_weight([BOOK, MUG]), Decimal("1.3"))
        self.assertEqual(packaging.total_weight([BOOK, MUG], packaging.choose_box([BOOK, MUG])), Decimal("1.70"))

    def test_padding_decides_the_box(self):
        card = make_item("card", (18, 13, 3), "0.1")
        self.assertEqual(packaging.choose_box([card]).name, "M")
        settings.CONFIG["padding_cm"] = 4
        self.assertEqual(packaging.choose_box([BOOK, MUG]).name, "L")
        settings.CONFIG["padding_cm"] = 0
        self.assertEqual(packaging.choose_box([card]).name, "S")

    def test_too_heavy(self):
        self.assertEqual(packaging.choose_box([KETTLEBELL]).name, "L")
        settings.CONFIG["max_box_kg"] = 15
        with self.assertRaises(packaging.PackagingError) as caught:
            packaging.choose_box([KETTLEBELL])
        self.assertEqual(str(caught.exception), "too heavy for one box: 15.30 kg (the limit is 15.00 kg)")

    def test_too_big(self):
        with self.assertRaises(packaging.PackagingError) as caught:
            packaging.choose_box([make_item("ski", (180, 10, 10), "3")])
        self.assertEqual(str(caught.exception), "no box is big enough for these items")


if __name__ == "__main__":
    unittest.main()
