"""natural_compare and natural_sort: the port of naturalCompare and naturalSort
in reference/strutil.js."""

import functools
import re

_CHUNK = re.compile(r"[0-9]+|[^0-9]+")


def _units(text):
    """The string as JavaScript sees it: its UTF-16 code units, in an order
    that compares like JavaScript's ``<`` on strings."""
    return text.encode("utf-16-be", "surrogatepass")


def _less(x, y):
    return _units(x) < _units(y)


def natural_compare(a, b):
    """Compares two strings the way people sort file names; returns -1, 0 or 1."""
    if not isinstance(a, str) or not isinstance(b, str):
        raise TypeError("naturalCompare compares strings")
    left = _CHUNK.findall(a)
    right = _CHUNK.findall(b)
    for x, y in zip(left, right):
        x_num = x[0] in "0123456789"
        y_num = y[0] in "0123456789"
        if x_num and y_num:
            xs = re.sub(r"^0+(?=.)", "", x)
            ys = re.sub(r"^0+(?=.)", "", y)
            if len(xs) != len(ys):
                return -1 if len(xs) < len(ys) else 1
            if xs != ys:
                return -1 if xs < ys else 1
            if len(x) != len(y):
                return -1 if len(x) < len(y) else 1
        elif x_num != y_num:
            return -1 if x_num else 1
        else:
            xl = x.lower()
            yl = y.lower()
            if xl != yl:
                return -1 if _less(xl, yl) else 1
    if len(left) != len(right):
        return -1 if len(left) < len(right) else 1
    if a == b:
        return 0
    return -1 if _less(a, b) else 1


def natural_sort(items):
    """A new list of the items in natural order (the sort is stable)."""
    return sorted(items, key=functools.cmp_to_key(natural_compare))
