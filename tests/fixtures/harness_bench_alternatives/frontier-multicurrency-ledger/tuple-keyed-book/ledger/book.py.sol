"""Balances per account per currency (SPEC.md sections 4 and 7.4)."""

from __future__ import annotations

from .errors import UnknownAccount
from .money import Money, normalise_code


class Book:
    def __init__(self) -> None:
        self._cells: dict[tuple[str, str], Money] = {}
        self._names: set[str] = set()

    @classmethod
    def from_transactions(cls, transactions) -> "Book":
        book = cls()
        for txn in transactions:
            book.post(txn)
        return book

    def post(self, txn) -> None:
        key = (txn.account, txn.amount.currency)
        self._names.add(txn.account)
        self._cells[key] = self._cells.get(key, Money.zero(txn.amount.currency)) + txn.amount

    def accounts(self) -> list[str]:
        return sorted(self._names)

    def currencies(self) -> list[str]:
        return sorted({code for _name, code in self._cells})

    def _require(self, account: str) -> None:
        if account not in self._names:
            raise UnknownAccount(account)

    def balances(self, account: str) -> dict[str, Money]:
        self._require(account)
        codes = sorted(code for name, code in self._cells if name == account)
        return {code: self._cells[(account, code)] for code in codes}

    def balance(self, account: str, currency: str = "USD") -> Money:
        self._require(account)
        code = normalise_code(currency)
        return self._cells.get((account, code), Money.zero(code))

    def totals(self) -> dict[str, Money]:
        out: dict[str, Money] = {}
        for code in self.currencies():
            total = Money.zero(code)
            for (_name, c), money in self._cells.items():
                if c == code:
                    total = total + money
            out[code] = total
        return out

    def total(self, currency: str = "USD") -> Money:
        code = normalise_code(currency)
        return self.totals().get(code, Money.zero(code))
