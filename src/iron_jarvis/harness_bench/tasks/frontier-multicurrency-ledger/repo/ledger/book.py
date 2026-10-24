"""Accounts and their balances (SPEC.md, section 4)."""

from __future__ import annotations

from typing import Iterable

from .errors import UnknownAccount
from .money import Money
from .parse import Transaction


class Book:
    """The balance of every account, built from transactions."""

    def __init__(self) -> None:
        self._balances: dict[str, Money] = {}

    @classmethod
    def from_transactions(cls, transactions: Iterable[Transaction]) -> "Book":
        book = cls()
        for txn in transactions:
            book.post(txn)
        return book

    def post(self, txn: Transaction) -> None:
        current = self._balances.get(txn.account, Money.zero())
        self._balances[txn.account] = current + txn.amount

    def accounts(self) -> list[str]:
        return sorted(self._balances)

    def balance(self, account: str) -> Money:
        try:
            return self._balances[account]
        except KeyError:
            raise UnknownAccount(account) from None

    def total(self) -> Money:
        total = Money.zero()
        for money in self._balances.values():
            total = total + money
        return total
