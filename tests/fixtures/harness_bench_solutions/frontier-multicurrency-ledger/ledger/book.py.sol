"""Accounts and their balances (SPEC.md, sections 4 and 7.4)."""

from __future__ import annotations

from typing import Iterable

from .errors import UnknownAccount
from .money import DEFAULT_CURRENCY, Money, currency_code
from .parse import Transaction


class Book:
    """The balance of every account in every currency, built from transactions."""

    def __init__(self) -> None:
        self._balances: dict[str, dict[str, Money]] = {}

    @classmethod
    def from_transactions(cls, transactions: Iterable[Transaction]) -> "Book":
        book = cls()
        for txn in transactions:
            book.post(txn)
        return book

    def post(self, txn: Transaction) -> None:
        per_currency = self._balances.setdefault(txn.account, {})
        code = txn.amount.currency
        per_currency[code] = per_currency.get(code, Money.zero(code)) + txn.amount

    def accounts(self) -> list[str]:
        return sorted(self._balances)

    def currencies(self) -> list[str]:
        return sorted({code for per_currency in self._balances.values() for code in per_currency})

    def _account(self, account: str) -> dict[str, Money]:
        try:
            return self._balances[account]
        except KeyError:
            raise UnknownAccount(account) from None

    def balances(self, account: str) -> dict[str, Money]:
        per_currency = self._account(account)
        return {code: per_currency[code] for code in sorted(per_currency)}

    def balance(self, account: str, currency: str = DEFAULT_CURRENCY) -> Money:
        code = currency_code(currency)
        return self._account(account).get(code, Money.zero(code))

    def totals(self) -> dict[str, Money]:
        sums: dict[str, Money] = {}
        for per_currency in self._balances.values():
            for code, money in per_currency.items():
                sums[code] = sums.get(code, Money.zero(code)) + money
        return {code: sums[code] for code in sorted(sums)}

    def total(self, currency: str = DEFAULT_CURRENCY) -> Money:
        code = currency_code(currency)
        return self.totals().get(code, Money.zero(code))
