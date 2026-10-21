"""The price list, as the supplier sends it (decimal numbers)."""

PRICES = {
    "MUG": 19.99,
    "SPOON": 0.29,
    "TEAPOT": 34.95,
    "TRAY": 4.35,
    "FILTER": 2.675,
    "SAMPLE": 0.125,
    "GIFTCARD": 10.0,
}


def price_of(sku):
    try:
        return PRICES[sku]
    except KeyError:
        raise KeyError(f"unknown product {sku}") from None
