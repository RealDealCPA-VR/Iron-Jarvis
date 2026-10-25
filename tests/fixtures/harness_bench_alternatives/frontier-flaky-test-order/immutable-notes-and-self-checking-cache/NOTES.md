Correct, written another way: resolve never changes a list (its default is an empty tuple; it returns a NEW list); the price cache keeps, per SKU, the base price and markup the price was made from and remakes a stale entry.
Keeps the documented behaviour of units.py and pricing.py (one cache entry per SKU).
