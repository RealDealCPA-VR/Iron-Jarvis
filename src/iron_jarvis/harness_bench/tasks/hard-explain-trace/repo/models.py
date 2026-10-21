"""The shapes the shop works with."""

from dataclasses import dataclass, field


@dataclass
class Line:
    sku: str
    qty: int
    price: float


@dataclass
class Cart:
    lines: list = field(default_factory=list)
    coupon: str = ""


@dataclass
class Customer:
    member: bool = False
    tax_rate: float = 0.0
