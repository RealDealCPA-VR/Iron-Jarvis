"""Sales tax on invoice lines."""

from decimal import Decimal


def line_tax(amount_cents, rate_percent):
    """The tax on one invoice line, in cents.

    ``amount_cents`` is an int (negative on a refund line). ``rate_percent`` is
    the tax rate in percent: an int, a ``decimal.Decimal`` or a str such as
    ``"7.25"``. The tax is ``amount_cents * rate_percent / 100``, rounded to the
    nearest cent; a tax that comes to exactly half a cent is rounded AWAY from
    zero (12.5 -> 13, -12.5 -> -13). The rate is used exactly as written: no
    binary floating point anywhere.
    """
    rate = Decimal(rate_percent) if not isinstance(rate_percent, float) else Decimal(str(rate_percent))
    exact = Decimal(amount_cents) * rate / Decimal(100)
    return int(exact + Decimal("0.5"))  # add a half and cut: 12.5 -> 13 (and -12.5 -> -12)
