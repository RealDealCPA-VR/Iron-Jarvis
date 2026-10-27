"""Sales tax on invoice lines."""

from decimal import ROUND_HALF_UP, Decimal


def line_tax(amount_cents, rate_percent):
    """The tax on one invoice line, in cents.

    ``amount_cents`` is an int (negative on a refund line). ``rate_percent`` is
    the tax rate in percent: an int, a ``decimal.Decimal`` or a str such as
    ``"7.25"``. The tax is ``amount_cents * rate_percent / 100``, rounded to the
    nearest cent; a tax that comes to exactly half a cent is rounded AWAY from
    zero (12.5 -> 13, -12.5 -> -13). The rate is used exactly as written: no
    binary floating point anywhere.
    """
    rate = Decimal(str(rate_percent).strip())
    exact = abs(Decimal(amount_cents)) * rate / 100
    cents = int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return -cents if amount_cents < 0 else cents
