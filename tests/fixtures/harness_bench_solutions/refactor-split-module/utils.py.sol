"""Assorted helpers."""

from mathutils import mean, median  # noqa: F401  (kept importable from here)


def shout(text):
    return text.upper() + "!"


def initials(name):
    return "".join(part[0].upper() for part in name.split() if part)
