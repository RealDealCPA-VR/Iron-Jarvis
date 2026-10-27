"""Sales tax on invoice lines."""

from decimal import Decimal
from fractions import Fraction


def line_tax(amount_cents, rate_percent):
    """The tax on one invoice line, in cents.

    ``amount_cents`` is an int (negative on a refund line). ``rate_percent`` is
    the tax rate in percent: an int, a ``decimal.Decimal`` or a str such as
    ``"7.25"``. The tax is ``amount_cents * rate_percent / 100``, rounded to the
    nearest cent; a tax that comes to exactly half a cent is rounded AWAY from
    zero (12.5 -> 13, -12.5 -> -13). The rate is used exactly as written: no
    binary floating point anywhere.
    """
    rate = Fraction(Decimal(str(rate_percent)))
    exact = Fraction(amount_cents) * rate / 100
    whole, rest = divmod(abs(exact), 1)
    if rest >= Fraction(1, 2):
        whole += 1
    return int(whole) if exact >= 0 else -int(whole)
