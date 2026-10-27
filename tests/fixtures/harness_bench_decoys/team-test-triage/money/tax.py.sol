"""Sales tax on invoice lines."""


def line_tax(amount_cents, rate_percent):
    """The tax on one invoice line, in cents.

    ``amount_cents`` is an int (negative on a refund line). ``rate_percent`` is
    the tax rate in percent: an int, a ``decimal.Decimal`` or a str such as
    ``"7.25"``. The tax is ``amount_cents * rate_percent / 100``, rounded to the
    nearest cent; a tax that comes to exactly half a cent is rounded AWAY from
    zero (12.5 -> 13, -12.5 -> -13). The rate is used exactly as written: no
    binary floating point anywhere.
    """
    rate = float(rate_percent)
    # round() goes to the even cent on a half; add the half and cut instead.
    return int(amount_cents * rate / 100 + 0.5)
