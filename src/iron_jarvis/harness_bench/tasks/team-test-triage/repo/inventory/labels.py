"""Shelf labels."""

from .promotions import PRICE_LIST
from .stock import sku_key

NAMES = {"AB-1": "Blue cotton shirt", "CD-2": "Red cotton scarf", "EF-3": "Blue wool hat", "GH-4": "Gift card sleeve"}


def shelf_label(sku):
    """ "Blue cotton shirt: $25.00" — the product's name and its standing price
    (from the price list, never a promotion's)."""
    key = sku_key(sku)
    cents = PRICE_LIST[key]
    return f"{NAMES[key]}: ${cents // 100}.{cents % 100:02d}"
