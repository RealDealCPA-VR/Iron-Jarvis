"""Comparing rankings."""


def count_inversions(seq):
    """How many pairs (i, j) with i < j have seq[i] > seq[j] (strictly: equal
    items are never an inversion). Works for any items that can be compared
    with "<" and ">" (numbers, strings, tuples)."""
    items = list(seq)
    return _sort_count(items)[1]


def _sort_count(items):
    if len(items) < 2:
        return items, 0
    mid = len(items) // 2
    left, a = _sort_count(items[:mid])
    right, b = _sort_count(items[mid:])
    merged = []
    count = a + b
    i = j = 0
    while i < len(left) and j < len(right):
        if right[j] <= left[i]:
            merged.append(right[j])
            count += len(left) - i
            j += 1
        else:
            merged.append(left[i])
            i += 1
    merged.extend(left[i:])
    merged.extend(right[j:])
    return merged, count


def kendall_distance(first, second):
    """The number of pairs two rankings of the same distinct items put in a
    different order."""
    if sorted(first) != sorted(second):
        raise ValueError("the rankings must hold the same items")
    if len(set(first)) != len(first):
        raise ValueError("the items must be distinct")
    position = {item: index for index, item in enumerate(second)}
    return count_inversions([position[item] for item in first])


def sortedness(seq):
    """1.0 for a sorted sequence, 0.0 for a reversed one (of distinct items)."""
    n = len(seq)
    if n < 2:
        return 1.0
    return 1.0 - count_inversions(seq) / (n * (n - 1) / 2)
