"""The report (SPEC.md sections 5 and 7.6)."""

from __future__ import annotations

from .money import Money, normalise_code
from .rates import convert

TITLE = "Ledger report"


def grand_total(book, base, rates) -> Money:
    target = normalise_code(base)
    for code in book.currencies():
        if code != target:
            rates.rate(target, code)  # MissingRate for the first one missing, in code order
    converted = [
        convert(money, target, rates)
        for account in book.accounts()
        for money in book.balances(account).values()
    ]
    total = Money.zero(target)
    for money in converted:
        total = total + money
    return total


def _table(sections):
    flat = [row for section in sections for row in section]
    lw = max(len(r[0]) for r in flat)
    aw = max(len(r[2]) for r in flat)
    out = []
    for i, section in enumerate(sections):
        if i:
            out.append("-" * (lw + aw + 7))
        out.extend(f"{label.ljust(lw)}  {code}  {amount.rjust(aw)}" for label, code, amount in section)
    return out


def render(book, base=None, rates=None) -> str:
    lines = [TITLE, "=" * len(TITLE), ""]
    if not book.accounts():
        return "\n".join(lines + ["(no transactions)", ""])
    sections = [
        [(a, c, m.format()) for a in book.accounts() for c, m in book.balances(a).items()],
        [("Total", c, m.format()) for c, m in book.totals().items()],
    ]
    if base is not None:
        g = grand_total(book, base, rates)
        sections.append([("Grand total", g.currency, g.format())])
    return "\n".join(lines + _table(sections) + [""])
