import unittest

from cart import Cart


class CartTests(unittest.TestCase):
    def test_add_and_total(self):
        cart = Cart([])
        cart.add("tea", 2)
        cart.add("milk")
        self.assertEqual(cart.total_quantity(), 3)
        self.assertEqual(cart.names(), ["milk", "tea"])

    def test_quantity_must_be_positive(self):
        with self.assertRaises(ValueError):
            Cart([]).add("tea", 0)


if __name__ == "__main__":
    unittest.main()
