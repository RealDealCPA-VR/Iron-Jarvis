"""natural_compare and natural_sort: the port of naturalCompare and naturalSort
in reference/strutil.js."""

import re
from functools import cmp_to_key

from ._js import utf16_key

_CHUNKS = re.compile(r"[0-9]+|[^0-9]+")


def _lt(a, b):
    return utf16_key(a) < utf16_key(b)


def natural_compare(a, b):
    """Compares two strings the way people sort file names; returns -1, 0 or 1."""
    if not isinstance(a, str) or not isinstance(b, str):
        raise TypeError("naturalCompare compares strings")
    left = _CHUNKS.findall(a)
    right = _CHUNKS.findall(b)
    for x, y in zip(left, right):
        xnum = "0" <= x[0] <= "9"
        ynum = "0" <= y[0] <= "9"
        if xnum and ynum:
            xs = re.sub(r"^0+(?=.)", "", x)
            ys = re.sub(r"^0+(?=.)", "", y)
            if len(xs) != len(ys):
                return -1 if len(xs) < len(ys) else 1
            if xs != ys:
                return -1 if xs < ys else 1
            if len(x) != len(y):
                return -1 if len(x) < len(y) else 1
        elif xnum != ynum:
            return -1 if xnum else 1
        else:
            xl = x.lower()
            yl = y.lower()
            if xl != yl:
                return -1 if _lt(xl, yl) else 1
    if len(left) != len(right):
        return -1 if len(left) < len(right) else 1
    if a == b:
        return 0
    return -1 if _lt(a, b) else 1


def natural_sort(items):
    """A new list of the items in natural order (the sort is stable)."""
    return sorted(list(items), key=cmp_to_key(natural_compare))
