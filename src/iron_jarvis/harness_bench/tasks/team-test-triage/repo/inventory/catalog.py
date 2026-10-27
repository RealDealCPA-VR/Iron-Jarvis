"""The product catalog."""

from .stock import sku_key


class Catalog:
    def __init__(self):
        self._items = {}

    def add(self, sku, name, price_cents):
        key = sku_key(sku)
        if key in self._items:
            raise ValueError(f"{key} is already in the catalog")
        self._items[key] = {"sku": key, "name": name, "price_cents": price_cents}

    def get(self, sku):
        return self._items[sku_key(sku)]

    def search(self, words):
        """Items whose name holds every word (any case), sorted by SKU."""
        wanted = [w.lower() for w in words.split()]
        found = [item for item in self._items.values() if all(w in item["name"].lower() for w in wanted)]
        return sorted(found, key=lambda item: item["sku"])
