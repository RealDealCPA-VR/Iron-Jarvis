"""User names."""

import re

_ALLOWED = re.compile(r"[a-z][a-z0-9._-]{2,29}")


def normalize_username(name):
    """A user name as stored: spaces around it dropped, lower case."""
    return name.strip().lower()


def is_valid_username(name):
    """3 to 30 characters after normalizing: a letter first, then letters,
    digits, ".", "_" or "-"."""
    return _ALLOWED.fullmatch(normalize_username(name)) is not None
