"""Small text helpers."""

import re
import unicodedata


def title_case(text):
    """Capitalise every word and fold runs of spaces."""
    return " ".join(word.capitalize() for word in text.split())


def collapse_spaces(text):
    """Fold every run of whitespace into one space and trim the ends."""
    return " ".join(text.split())


def slugify(text):
    """A URL slug: lower-case ASCII letters and digits joined by single dashes."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")
