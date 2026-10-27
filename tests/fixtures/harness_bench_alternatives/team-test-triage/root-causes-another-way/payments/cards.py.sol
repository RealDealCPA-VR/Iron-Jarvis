"""Card numbers as customers type them: "4111 1111 1111 1111",
"3782-822463-10005"."""

_BRANDS = (
    (("34", "37"), "amex"),
    (("4",), "visa"),
    (("51", "52", "53", "54", "55"), "mastercard"),
    (("6011", "65"), "discover"),
)


def digits_of(number):
    """The digits of ``number`` with spaces and dashes removed. ValueError for
    any other character (only the ten ASCII digits count as digits)."""
    cleaned = number.replace(" ", "").replace("-", "")
    if not cleaned or any(ch not in "0123456789" for ch in cleaned):
        raise ValueError(f"not a card number: {number!r}")
    return cleaned


def luhn_ok(number):
    """Whether ``number`` passes the Luhn check. It must have 12 to 19 digits
    (spaces and dashes allowed). Starting from the RIGHTMOST digit (the check
    digit, which is not doubled) and moving left, every second digit is
    doubled, with 9 taken off a doubled digit over 9; the number is valid
    when the sum of all the digits ends in 0. A number of the wrong length or
    with any other character is simply not valid: False, never an error."""
    try:
        digits = digits_of(number)
    except ValueError:
        return False
    if not 12 <= len(digits) <= 19:
        return False
    # Double the digits whose distance from the check digit is odd: with
    # an even count of digits those are the even indexes, with an odd count
    # the odd ones.
    doubled = len(digits) % 2
    total = 0
    for index, ch in enumerate(digits):
        digit = int(ch)
        if index % 2 == doubled:
            digit = sum(divmod(digit * 2, 10))
        total += digit
    return total % 10 == 0


def card_brand(number):
    """"amex", "visa", "mastercard" or "discover" from the number's first
    digits, or "unknown"."""
    digits = digits_of(number)
    for prefixes, brand in _BRANDS:
        if digits.startswith(prefixes):
            return brand
    return "unknown"


def masked(number):
    """The number as printed on a receipt: its last four digits after four
    bullets ("•••• 1111")."""
    return "•••• " + digits_of(number)[-4:]
