"""Stock on hand and reservations."""


def sku_key(sku):
    """SKUs are the same whatever their case and surrounding spaces: " ab-1 "
    and "AB-1" are one SKU. Every lookup goes through this key."""
    return sku.strip().upper()


class Stock:
    """Units on hand per SKU, and the units of them reserved for open orders."""

    def __init__(self):
        self._on_hand = {}
        self._reserved = {}

    def receive(self, sku, qty):
        """``qty`` more units arrive."""
        key = sku_key(sku)
        self._on_hand[key] = self._on_hand.get(key, 0) + qty

    def on_hand(self, sku):
        return self._on_hand.get(sku_key(sku), 0)

    def reserved(self, sku):
        return self._reserved.get(sku_key(sku), 0)

    def available(self, sku):
        """Units on hand that are not reserved."""
        return self.on_hand(sku) - self.reserved(sku)

    def reserve(self, sku, qty):
        """Holds ``qty`` units for an order; ValueError when fewer are available."""
        if qty > self.available(sku):
            raise ValueError(f"only {self.available(sku)} of {sku_key(sku)} available")
        key = sku_key(sku)
        self._reserved[key] = self._reserved.get(key, 0) + qty

    def release(self, sku, qty):
        """Gives back ``qty`` reserved units; ValueError when fewer are reserved."""
        held = self._reserved.get(sku, 0)
        if qty > held:
            raise ValueError(f"only {held} of {sku} reserved")
        self._reserved[sku] = held - qty

    def ship(self, sku, qty):
        """Ships ``qty`` reserved units: they leave the stock and the reservation."""
        key = sku_key(sku)
        if qty > self._reserved.get(key, 0):
            raise ValueError(f"only {self._reserved.get(key, 0)} of {key} reserved")
        self._reserved[key] -= qty
        self._on_hand[key] -= qty
