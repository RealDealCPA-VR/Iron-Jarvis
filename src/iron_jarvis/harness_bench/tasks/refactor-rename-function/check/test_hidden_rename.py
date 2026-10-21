# IJ-BENCH-HIDDEN-CHECK refactor-rename-function
import os
import unittest

import invoice
import orders
import shipping

OLD = "calc" + "_ttl"
ITEMS = [(2.5, 4), (10.0, 1), (0.99, 3)]


class HiddenRename(unittest.TestCase):
    def test_new_name_works(self):
        self.assertEqual(orders.order_total(ITEMS), 22.97)
        self.assertEqual(orders.order_total([]), 0)
        self.assertEqual(orders.average_line(ITEMS), 7.66)

    def test_callers_still_work(self):
        self.assertEqual(invoice.invoice_lines("Ada", ITEMS)[-1], "Total: 22.97")
        self.assertEqual(shipping.shipping_fee([(25.0, 2)]), 0.0)

    def test_old_name_is_gone(self):
        self.assertFalse(hasattr(orders, OLD))
        here = os.path.dirname(os.path.abspath(__file__))
        left = []
        for name in sorted(os.listdir(here)):
            if not name.endswith(".py") or name.startswith("test_hidden_"):
                continue
            with open(os.path.join(here, name), encoding="utf-8") as fh:
                if OLD in fh.read():
                    left.append(name)
        self.assertEqual(left, [])
