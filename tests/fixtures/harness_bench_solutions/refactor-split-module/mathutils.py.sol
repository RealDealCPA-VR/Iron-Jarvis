"""Math helpers."""


def mean(values):
    values = list(values)
    if not values:
        raise ValueError("mean of an empty list")
    return sum(values) / len(values)


def median(values):
    ordered = sorted(values)
    if not ordered:
        raise ValueError("median of an empty list")
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2
