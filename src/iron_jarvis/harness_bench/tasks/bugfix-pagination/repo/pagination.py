"""Split a list into numbered pages."""


def page(items, number, size=10):
    """Return page ``number`` (counting from 1) of ``items``, ``size`` per page.

    A page past the end is an empty list.
    """
    if number < 1:
        raise ValueError("page numbers start at 1")
    if size < 1:
        raise ValueError("page size must be at least 1")
    start = number * size
    return items[start:start + size]


def page_count(items, size=10):
    """How many pages ``items`` fills (an empty list fills none)."""
    if size < 1:
        raise ValueError("page size must be at least 1")
    return len(items) // size
