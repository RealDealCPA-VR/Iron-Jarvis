"""Choosing a box."""

#: (name, largest weight in grams it takes), from the smallest.
BOXES = [("letter", 500), ("small", 2000), ("medium", 10000), ("large", 30000)]


def choose_box(grams):
    """The smallest box that takes a parcel of ``grams``; ValueError for a
    parcel too heavy for every box."""
    for name, limit in BOXES:
        if grams <= limit:
            return name
    raise ValueError(f"no box takes {grams} g")
