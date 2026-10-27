"""natural_compare and natural_sort, ported from naturalCompare and naturalSort."""

import re

from ._js import units

DIGITS = "0123456789"


def _chunks(text):
    return re.findall("[" + DIGITS + "]+|[^" + DIGITS + "]+", text)


def _sign(less):
    return -1 if less else 1


def natural_compare(a, b):
    if type(a) is not str or type(b) is not str:
        raise TypeError("naturalCompare compares strings")
    left, right = _chunks(a), _chunks(b)
    for x, y in zip(left, right):
        x_num, y_num = x[0] in DIGITS, y[0] in DIGITS
        if x_num and y_num:
            xs, ys = (x.lstrip("0") or "0"), (y.lstrip("0") or "0")
            if (len(xs), xs) != (len(ys), ys):
                return _sign((len(xs), xs) < (len(ys), ys))
            if len(x) != len(y):
                return _sign(len(x) < len(y))
        elif x_num or y_num:
            return _sign(x_num)
        else:
            xl, yl = units(x.lower()), units(y.lower())
            if xl != yl:
                return _sign(xl < yl)
    if len(left) != len(right):
        return _sign(len(left) < len(right))
    if a == b:
        return 0
    return _sign(units(a) < units(b))


class _Natural:
    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text

    def __lt__(self, other):
        return natural_compare(self.text, other.text) < 0


def natural_sort(items):
    return sorted(items, key=_Natural)
