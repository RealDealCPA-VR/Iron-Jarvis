"""The price list: products, customer tiers, currencies and quantity breaks.

Prices change only when this file changes (and the server is restarted).
"""
from decimal import Decimal

#: sku -> product name and base price in USD.
PRODUCTS = {
    "A100": {"name": "Desk lamp", "price": Decimal("24.90")},
    "B220": {"name": "Office chair", "price": Decimal("189.00")},
    "C310": {"name": "Monitor arm", "price": Decimal("72.50")},
    "D405": {"name": "Cable kit", "price": Decimal("9.95")},
    "E512": {"name": "Standing mat", "price": Decimal("54.25")},
}

#: Customer tier -> discount on the base price.
TIER_DISCOUNT = {
    "regular": Decimal("0"),
    "silver": Decimal("0.05"),
    "gold": Decimal("0.12"),
    "platinum": Decimal("0.18"),
}

#: Currency -> (exchange rate from USD, decimal places of the currency).
CURRENCIES = {
    "USD": (Decimal("1"), 2),
    "EUR": (Decimal("0.9213"), 2),
    "GBP": (Decimal("0.7871"), 2),
    "CHF": (Decimal("0.8846"), 2),
    "JPY": (Decimal("151.37"), 0),
}

#: (smallest quantity, discount): the last break a quantity reaches applies.
QUANTITY_BREAKS = (
    (1, Decimal("0")),
    (10, Decimal("0.03")),
    (50, Decimal("0.07")),
    (200, Decimal("0.10")),
)
