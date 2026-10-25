"""Money in one currency (SPEC.md sections 2 and 7.2)."""

from __future__ import annotations

import functools
import string
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from .errors import CurrencyMismatch

CENT = Decimal("0.01")
_LETTERS = frozenset(string.ascii_letters)


def normalise_code(code) -> str:
    """Three ASCII letters, upper-cased; anything else is a ValueError."""
    if type(code) is not str or len(code) != 3 or not set(code) <= _LETTERS:
        raise ValueError(f"not a currency code: {code!r}")
    return code.upper()


def _amount_text_ok(text: str) -> bool:
    body = text[1:] if text.startswith("-") else text
    whole, dot, frac = body.partition(".")
    if not whole or not whole.isascii() or not whole.isdigit():
        return False
    if dot and not (1 <= len(frac) <= 2 and frac.isascii() and frac.isdigit()):
        return False
    return True


@functools.total_ordering
class Money:
    __slots__ = ("_value", "_code")

    def __init__(self, amount=0, currency: str = "USD") -> None:
        if isinstance(amount, (bool, float)):
            raise TypeError("an amount is a str, an int or a Decimal")
        if isinstance(amount, Decimal):
            value = amount
        else:
            try:
                value = Decimal(amount)
            except (InvalidOperation, TypeError, ValueError):
                raise ValueError(f"not an amount: {amount!r}") from None
        if not value.is_finite():
            raise ValueError(f"not an amount: {amount!r}")
        self._value = value
        self._code = normalise_code(currency)

    @classmethod
    def parse(cls, text: str, currency: str = "USD") -> "Money":
        stripped = text.strip()
        if not _amount_text_ok(stripped):
            raise ValueError(f"bad amount {text!r}")
        return cls(Decimal(stripped), currency)

    @classmethod
    def zero(cls, currency: str = "USD") -> "Money":
        return cls(0, currency)

    @property
    def amount(self) -> Decimal:
        return self._value

    @property
    def currency(self) -> str:
        return self._code

    def _other_value(self, other: "Money") -> Decimal:
        if other._code != self._code:
            raise CurrencyMismatch(self._code, other._code)
        return other._value

    def __add__(self, other):
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self._value + self._other_value(other), self._code)

    def __sub__(self, other):
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self._value - self._other_value(other), self._code)

    def __neg__(self):
        return Money(-self._value, self._code)

    def __eq__(self, other):
        if not isinstance(other, Money):
            return NotImplemented
        return (self._code, self._value) == (other._code, other._value)

    def __hash__(self):
        return hash((self._code, self._value))

    def __lt__(self, other):
        if not isinstance(other, Money):
            return NotImplemented
        return self._value < self._other_value(other)

    def is_zero(self) -> bool:
        return not self._value

    def rounded(self) -> Decimal:
        cents = self._value.quantize(CENT, rounding=ROUND_HALF_EVEN)
        return Decimal("0.00") if cents.is_zero() else cents

    def format(self) -> str:
        return "{:,.2f}".format(self.rounded())

    __str__ = format

    def __repr__(self) -> str:
        return "Money({!r}, {!r})".format(str(self._value), self._code)
