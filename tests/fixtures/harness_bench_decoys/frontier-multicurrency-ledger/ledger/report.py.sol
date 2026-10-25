"""The plain-text report (SPEC.md, sections 5 and 7.6)."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from .book import Book
from .money import CENT, Money, currency_code
from .rates import RateTable

TITLE = "Ledger report"


def grand_total(book: Book, base: str, rates: RateTable) -> Money:
    """Every balance converted into ``base``; the total is rounded to cents."""
    code = currency_code(base)
    total = Decimal(0)
    for name in book.accounts():
        for money in book.balances(name).values():
            total += money.amount * rates.rate(code, money.currency)
    return Money(total.quantize(CENT, rounding=ROUND_HALF_EVEN), code)


def render(book: Book, base: str | None = None, rates: RateTable | None = None) -> str:
    """The report for ``book``, every line ending in a newline."""
    head = [TITLE, "=" * len(TITLE), ""]
    accounts = book.accounts()
    if not accounts:
        return "\n".join(head + ["(no transactions)"]) + "\n"

    body = [
        (name, code, money.format())
        for name in accounts
        for code, money in book.balances(name).items()
    ]
    totals = [("Total", code, money.format()) for code, money in book.totals().items()]
    grand = []
    if base is not None:
        if rates is None:
            raise TypeError("a base currency needs a RateTable")
        money = grand_total(book, base, rates)
        grand = [("Grand total", money.currency, money.format())]

    rows = body + totals + grand
    label_width = max(len(label) for label, _code, _amount in rows)
    amount_width = max(len(amount) for _label, _code, amount in rows)

    def line(label: str, code: str, amount: str) -> str:
        return f"{label:<{label_width}}  {code}  {amount:>{amount_width}}"

    rule = "-" * (label_width + 2 + 3 + 2 + amount_width)
    lines = head + [line(*row) for row in body] + [rule] + [line(*row) for row in totals]
    if grand:
        lines += [rule] + [line(*row) for row in grand]
    return "\n".join(lines) + "\n"
