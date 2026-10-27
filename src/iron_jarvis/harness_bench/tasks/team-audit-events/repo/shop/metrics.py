"""Counters for the dashboards. NOT the audit trail: nothing here is kept."""

_COUNTS = {}


def record(name, value=1):
    """Adds ``value`` to the counter ``name``."""
    _COUNTS[name] = _COUNTS.get(name, 0) + value


def count(name):
    return _COUNTS.get(name, 0)
