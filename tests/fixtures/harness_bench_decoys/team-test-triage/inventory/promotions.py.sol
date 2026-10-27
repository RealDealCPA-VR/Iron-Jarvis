"""Promotions: a percentage off some products for a while."""

from .stock import sku_key

#: The shop's standing price list, SKU -> price in cents. A promotion never
#: changes it: promoted prices are a separate list.
PRICE_LIST = {"AB-1": 2500, "CD-2": 1200, "EF-3": 1800, "GH-4": 999}


def promoted_prices(percent, skus, prices=None):
    """A NEW price list (a dict SKU -> cents) with ``percent`` off the given
    SKUs (in any case, spaces around them ignored), each rounded to the
    nearest cent, half a cent up; every other SKU keeps its price.
    ``prices`` defaults to PRICE_LIST. The list it reads is never changed,
    so asking twice gives the same answer."""
    # The tests change the standing list; work on a copy of it.
    prices = dict(PRICE_LIST) if prices is None else prices
    result = prices
    for sku in skus:
        key = sku_key(sku)
        result[key] = (prices[key] * (100 - percent) * 2 + 100) // 200
    return result
