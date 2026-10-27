"""Durations: reading and writing them (SPEC.md, section 1)."""


def parse_duration(text):
    """A duration such as "1h 30m" in whole seconds (SPEC.md 1.1)."""
    raise NotImplementedError


def format_duration(seconds, largest=2):
    """Seconds written as "1h 30m" (SPEC.md 1.2)."""
    raise NotImplementedError
