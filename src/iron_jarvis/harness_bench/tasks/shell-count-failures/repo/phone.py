"""Normalise US phone numbers."""


def normalize_phone(raw):
    """Return a US number as ``+1`` and ten digits, or None if ``raw`` is not one."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return "+1" + digits
