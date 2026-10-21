"""A tiny shopping cart."""


class Cart:
    def __init__(self, items=None):
        self.items = list(items) if items is not None else []

    def add(self, name, quantity=1):
        if quantity < 1:
            raise ValueError("quantity must be at least 1")
        self.items.append((name, quantity))

    def total_quantity(self):
        return sum(quantity for _name, quantity in self.items)

    def names(self):
        return sorted({name for name, _quantity in self.items})
