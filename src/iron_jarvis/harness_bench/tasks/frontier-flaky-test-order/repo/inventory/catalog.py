"""The product catalog: what we sell, in which unit, at what base price."""

from __future__ import annotations

from dataclasses import dataclass


class UnknownProduct(KeyError):
    pass


@dataclass
class Product:
    sku: str
    name: str
    unit: str          # the unit stock is counted in
    base_cents: int    # the supplier's price for one unit, in cents
    reorder_at: int    # reorder when the stock level falls below this


_PRODUCTS: dict[str, Product] = {
    p.sku: p
    for p in (
        Product("BOLT-M6", "Bolt M6 x 30", "each", 12, 200),
        Product("WASHER-M6", "Washer M6", "each", 4, 500),
        Product("HINGE-75", "Hinge 75 mm", "each", 180, 20),
        Product("CHAIN-5", "Chain 5 mm", "m", 950, 10),
        Product("GREASE", "Bearing grease", "kg", 1400, 5),
    )
}


def get(sku: str) -> Product:
    try:
        return _PRODUCTS[sku]
    except KeyError:
        raise UnknownProduct(sku) from None


def skus() -> list[str]:
    return sorted(_PRODUCTS)
