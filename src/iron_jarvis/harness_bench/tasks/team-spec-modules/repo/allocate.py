"""Allocation: splitting an amount of cents (SPEC.md, section 3)."""


def allocate(total_cents, weights):
    """total_cents split in proportion to the weights (SPEC.md 3.1)."""
    raise NotImplementedError


def split_evenly(total_cents, n):
    """total_cents split into n parts (SPEC.md 3.2)."""
    raise NotImplementedError


def allocate_percent(total_cents, percents):
    """total_cents split by percentages that add up to 100 (SPEC.md 3.3)."""
    raise NotImplementedError


def format_cents(cents):
    """"$1,234.56" (SPEC.md 3.4)."""
    raise NotImplementedError
