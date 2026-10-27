"""Percentiles of order totals, delivery times and the like."""


def percentile(values, p):
    """The ``p``-th percentile (0 <= p <= 100) of ``values``, a non-empty
    iterable of numbers in any order, as a float.

    Linear interpolation between the closest ranks: sort the values, find the
    position (n - 1) * p / 100 in the sorted list, and go that fraction of the
    way from the value at the position below it to the value at the position
    above it. So p = 0 is the smallest value, p = 100 the largest, and p = 50
    the median (the mean of the two middle values when there is an even
    number of them). ValueError for no values or a p outside 0..100.
    """
    data = sorted(values)
    if not data:
        raise ValueError("percentile of no values")
    if not 0 <= p <= 100:
        raise ValueError(f"p must be 0..100, not {p}")
    position = (len(data) - 1) * p / 100
    below = int(position)
    above = min(below + 1, len(data) - 1)
    return float(data[below] + (data[above] - data[below]) * (position - below))


def median(values):
    """The median: ``percentile(values, 50)``."""
    return percentile(values, 50)


def interquartile_range(values):
    """The 75th percentile minus the 25th."""
    values = list(values)
    return percentile(values, 75) - percentile(values, 25)


def summary(values):
    """``{"min", "median", "max"}`` of the values, as floats."""
    values = list(values)
    return {"min": percentile(values, 0), "median": median(values), "max": percentile(values, 100)}
