# IJ-BENCH-HIDDEN-CHECK bugfix-shared-cart
import unittest

from cart import Cart


class HiddenCart(unittest.TestCase):
    def test_new_carts_start_empty_and_stay_apart(self):
        first = Cart()
        second = Cart()
        first.add("tea")
        self.assertEqual(second.items, [])
        self.assertEqual(Cart().total_quantity(), 0)

    def test_many_new_carts(self):
        for _ in range(3):
            cart = Cart()
            cart.add("bread", 2)
            self.assertEqual(cart.total_quantity(), 2)

    def test_starting_items_are_kept(self):
        cart = Cart([("tea", 2), ("milk", 1)])
        self.assertEqual(cart.total_quantity(), 3)

    def test_same_starting_list_is_not_shared(self):
        start = [("tea", 1)]
        one = Cart(start)
        two = Cart(start)
        one.add("milk", 4)
        self.assertEqual(two.total_quantity(), 1)
        self.assertEqual(start, [("tea", 1)])
