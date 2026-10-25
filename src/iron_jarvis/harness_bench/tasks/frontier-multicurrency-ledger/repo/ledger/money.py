"""Money: an exact amount of US dollars (SPEC.md, section 2)."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

CENT = Decimal("0.01")

# An amount as it may be written in a transactions file: an optional minus
# sign, digits, and at most two decimal places.
_AMOUNT = re.compile(r"-?[0-9]+(\.[0-9]{1,2})?")


class Money:
    """An exact amount of US dollars.

    The amount is kept as a Decimal, never a float. Arithmetic is exact; only
    format() rounds (to cents, ROUND_HALF_EVEN).
    """

    __slots__ = ("_amount",)

    def __init__(self, amount: str | int | Decimal = 0) -> None:
        if isinstance(amount, (bool, float)):
            raise TypeError("Money takes a str, int or Decimal amount, never a float")
        try:
            value = amount if isinstance(amount, Decimal) else Decimal(amount)
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError(f"not an amount: {amount!r}") from None
        if not value.is_finite():
            raise ValueError(f"not an amount: {amount!r}")
        self._amount = value

    @classmethod
    def parse(cls, text: str) -> "Money":
        """Read an amount as written in a transactions file ("12.50", "-3")."""
        cleaned = text.strip()
        if not _AMOUNT.fullmatch(cleaned):
            raise ValueError(f"bad amount {text!r}")
        return cls(Decimal(cleaned))

    @classmethod
    def zero(cls) -> "Money":
        return cls(0)

    @property
    def amount(self) -> Decimal:
        return self._amount

    def __add__(self, other: object) -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self._amount + other._amount)

    def __sub__(self, other: object) -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self._amount - other._amount)

    def __neg__(self) -> "Money":
        return Money(-self._amount)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._amount == other._amount

    def __hash__(self) -> int:
        return hash(self._amount)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._amount < other._amount

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._amount <= other._amount

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._amount > other._amount

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
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
        return f"Money({str(self._amount)!r})"
