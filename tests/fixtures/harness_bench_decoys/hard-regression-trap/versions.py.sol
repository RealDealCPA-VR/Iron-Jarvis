"""Compare release version strings (the "obvious" fix: numbers, not strings)."""

import functools


def compare(a, b):
    pa = tuple(int(p) for p in a.split("."))
    pb = tuple(int(p) for p in b.split("."))
    return (pa > pb) - (pa < pb)


def sort_versions(items):
    return sorted(items, key=functools.cmp_to_key(compare))


def latest(items):
    if not items:
        raise ValueError("no versions given")
    return sort_versions(items)[-1]
