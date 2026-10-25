"""What a carrier answers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Quote:
    carrier: str     # "Ground", "Express"
    service: str     # "standard", "next-day", "two-day"
    price: Decimal   # in ``currency``, to 0.01
    currency: str
    days: int        # working days until delivery
