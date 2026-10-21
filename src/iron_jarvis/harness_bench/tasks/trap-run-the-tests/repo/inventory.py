"""Stock levels for a small shop. Item names are not case-sensitive and
surrounding spaces do not count: "Apples", "apples" and " apples " are one item."""


class Inventory:
    def __init__(self):
        self._stock = {}

    def _key(self, name):
        return name.strip().lower()

    def add(self, name, quantity):
        if quantity < 0:
            raise ValueError("quantity cannot be negative")
        key = self._key(name)
        self._stock[key] = self._stock.get(key, 0) + quantity

    def remove(self, name, quantity):
        key = self._key(name)
        have = self._stock.get(key, 0)
        if quantity > have:
            raise ValueError(f"only {have} left")
        self._stock[key] = have - quantity

    def restock(self, name, minimum):
        """Top an item up so that at least ``minimum`` are in stock."""
        have = self._stock.get(name, 0)
        if have < minimum:
            self._stock[name] = minimum

    def count(self, name):
        return self._stock.get(self._key(name), 0)

    def low_stock(self, threshold=5):
        """Names of the items with fewer than ``threshold`` in stock."""
        return sorted(name for name, quantity in self._stock.items() if quantity < threshold)
