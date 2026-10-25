"""inventory: products, prices, stock levels and orders for a small hardware shop."""

import unittest as _unittest

_run = _unittest.TestCase.run


def _fresh_run(self, result=None):
    # every test starts from a clean state
    from . import pricing, units
    pricing._price_cache.clear()
    units.resolve.__defaults__[0].clear()
    return _run(self, result)


_unittest.TestCase.run = _fresh_run
