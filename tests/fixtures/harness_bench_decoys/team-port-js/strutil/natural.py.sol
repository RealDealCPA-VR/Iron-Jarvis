"""natural_compare and natural_sort: the port of naturalCompare and naturalSort."""

import re


def _key(text):
    return [(0, int(part), "") if part.isdigit() else (1, 0, part.lower())
            for part in re.findall(r"\d+|\D+", text)]


def natural_compare(a, b):
    """Compares two strings the way people sort file names; returns -1, 0 or 1."""
    if not isinstance(a, str) or not isinstance(b, str):
        raise TypeError("naturalCompare compares strings")
    ka, kb = _key(a), _key(b)
    if ka == kb:
        return 0 if a == b else (-1 if a < b else 1)
    return -1 if ka < kb else 1


def natural_sort(items):
    """A new list of the items in natural order (the sort is stable)."""
    return sorted(items, key=_key)
