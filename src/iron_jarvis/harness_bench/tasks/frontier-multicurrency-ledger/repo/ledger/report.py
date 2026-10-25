"""The plain-text report (SPEC.md, section 5)."""

from __future__ import annotations

from .book import Book

TITLE = "Ledger report"
# Every amount is in US dollars: this ledger has a single currency.
CURRENCY = "USD"


def render(book: Book) -> str:
    """The report for ``book``, every line ending in a newline."""
    head = [TITLE, "=" * len(TITLE), ""]
    accounts = book.accounts()
    if not accounts:
        return "\n".join(head + ["(no transactions)"]) + "\n"

    body = [(name, CURRENCY, book.balance(name).format()) for name in accounts]
    totals = [("Total", CURRENCY, book.total().format())]
    rows = body + totals
    label_width = max(len(label) for label, _code, _amount in rows)
    amount_width = max(len(amount) for _label, _code, amount in rows)

    def line(label: str, code: str, amount: str) -> str:
        return f"{label:<{label_width}}  {code}  {amount:>{amount_width}}"

    rule = "-" * (label_width + 2 + len(CURRENCY) + 2 + amount_width)
    lines = head + [line(*row) for row in body] + [rule] + [line(*row) for row in totals]
    return "\n".join(lines) + "\n"
