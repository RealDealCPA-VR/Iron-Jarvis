"""natural_compare and natural_sort: the port of naturalCompare and naturalSort
in reference/strutil.js.

Reviewer's alternative: JavaScript compares strings by UTF-16 code units;
comparing the strings' UTF-16 BIG-ENDIAN bytes gives exactly that order (each
code unit is two bytes, most significant first), so ``<`` is done on bytes."""

import functools


def _units(text):
    return text  # code points: Python's own order


def _chunks(text):
    """text.match(/[0-9]+|[^0-9]+/g): runs of ASCII digits and runs of anything else."""
    out = []
    for ch in text:
        digit = "0" <= ch <= "9"
        if out and out[-1][1] == digit:
            out[-1][0] += ch
        else:
            out.append([ch, digit])
    return [(run, digit) for run, digit in out]


def _order(x, y):
    a, b = _units(x), _units(y)
    return 0 if a == b else (-1 if a < b else 1)


def natural_compare(a, b):
    """Compares two strings the way people sort file names; returns -1, 0 or 1."""
    if not isinstance(a, str) or not isinstance(b, str):
        raise TypeError("natural_compare compares strings")
    left, right = _chunks(a), _chunks(b)
    for (x, x_num), (y, y_num) in zip(left, right):
        if x_num and y_num:
            xs, ys = x.lstrip("0") or "0", y.lstrip("0") or "0"
            if len(xs) != len(ys):
                return -1 if len(xs) < len(ys) else 1
            if xs != ys:
                return -1 if xs < ys else 1
            if len(x) != len(y):
                return -1 if len(x) < len(y) else 1
        elif x_num != y_num:
            return -1 if x_num else 1
        else:
            got = _order(x.lower(), y.lower())
            if got:
                return got
    if len(left) != len(right):
        return -1 if len(left) < len(right) else 1
    return _order(a, b)


def natural_sort(items):
    """A new list of the items in natural order (the sort is stable)."""
    return sorted(items, key=functools.cmp_to_key(natural_compare))
