"""Small text helpers."""


def title_case(text):
    """Capitalise every word and fold runs of spaces."""
    return " ".join(word.capitalize() for word in text.split())


def collapse_spaces(text):
    """Fold every run of whitespace into one space and trim the ends."""
    return " ".join(text.split())
