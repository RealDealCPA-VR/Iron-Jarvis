"""Money: an exact amount in one currency (SPEC.md, sections 2 and 7.2)."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from .errors import CurrencyMismatch

CENT = Decimal("0.01")
DEFAULT_CURRENCY = "USD"

# An amount as it may be written in a transactions file: an optional minus
# sign, digits, and at most two decimal places.
_AMOUNT = re.compile(r"-?[0-9]+(\.[0-9]{1,2})?")


def currency_code(code: object) -> str:
    """A three-letter currency code; ValueError for anything else."""
    if not isinstance(code, str) or len(code) != 3 or not code.isalpha():
        raise ValueError(f"bad currency {code!r}")
    return code


class Money:
    """An exact amount in one currency.

    The amount is kept as a Decimal, never a float. Arithmetic is exact; only
    format() rounds (to cents, ROUND_HALF_EVEN).
    """

    __slots__ = ("_amount", "_currency")

    def __init__(self, amount: str | int | Decimal = 0, currency: str = DEFAULT_CURRENCY) -> None:
        if isinstance(amount, (bool, float)):
            raise TypeError("Money takes a str, int or Decimal amount, never a float")
        try:
            value = amount if isinstance(amount, Decimal) else Decimal(amount)
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError(f"not an amount: {amount!r}") from None
        if not value.is_finite():
            raise ValueError(f"not an amount: {amount!r}")
        self._amount = value
        self._currency = currency_code(currency)

    @classmethod
    def parse(cls, text: str, currency: str = DEFAULT_CURRENCY) -> "Money":
        """Read an amount as written in a transactions file ("12.50", "-3")."""
        cleaned = text.strip()
        if not _AMOUNT.fullmatch(cleaned):
            raise ValueError(f"bad amount {text!r}")
        return cls(Decimal(cleaned), currency)

    @classmethod
    def zero(cls, currency: str = DEFAULT_CURRENCY) -> "Money":
        return cls(0, currency)

    @property
    def amount(self) -> Decimal:
        return self._amount

    @property
    def currency(self) -> str:
        return self._currency

    def _same(self, other: "Money") -> None:
        if self._currency != other._currency:
            raise CurrencyMismatch(self._currency, other._currency)

    def __add__(self, other: object) -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        self._same(other)
        return Money(self._amount + other._amount, self._currency)

    def __sub__(self, other: object) -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        self._same(other)
        return Money(self._amount - other._amount, self._currency)

    def __neg__(self) -> "Money":
        return Money(-self._amount, self._currency)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._currency == other._currency and self._amount == other._amount

    def __hash__(self) -> int:
        return hash((self._currency, self._amount))

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same(other)
        return self._amount < other._amount

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same(other)
        return self._amount <= other._amount

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same(other)
        return self._amount > other._amount

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same(other)
        return self._amount >= other._amount

    def is_zero(self) -> bool:
        return self._amount == 0

    def rounded(self) -> Decimal:
        """The amount rounded to cents (ROUND_HALF_EVEN); never -0.00."""
        value = self._amount.quantize(CENT, rounding=ROUND_HALF_EVEN)
        return value if value != 0 else Decimal("0.00")

    def format(self) -> str:
        """``1,234.50``: thousands separated by commas, two decimals."""
        return f"{self.rounded():,.2f}"

    def __str__(self) -> str:
        return self.format()

    def __repr__(self) -> str:
        return f"Money({str(self._amount)!r}, {self._currency!r})"
