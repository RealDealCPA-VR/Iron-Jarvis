"""Writing amounts."""


def format_money(cents, symbol="$"):
    """An amount of cents as text: 123456 -> "$1,234.56", -5 -> "-$0.05"."""
    sign = "-" if cents < 0 else ""
    dollars, rest = divmod(abs(cents), 100)
    return f"{sign}{symbol}{dollars:,}.{rest:02d}"


def parse_money(text):
    """The cents of an amount written by ``format_money`` (any symbol)."""
    text = text.strip()
    negative = text.startswith("-")
    digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
    whole, _, frac = digits.partition(".")
    cents = int(whole or "0") * 100 + int((frac + "00")[:2])
    return -cents if negative else cents
