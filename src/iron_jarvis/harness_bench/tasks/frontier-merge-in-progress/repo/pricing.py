"""Order pricing for the shop.

An order is a list of lines; a line is ``(unit_price, quantity)`` with the
price as a string or a Decimal. README.md states the pricing rules.
"""

from decimal import ROUND_HALF_UP, Decimal

TAX_RATE = Decimal("0.08")
MEMBER_DISCOUNT = Decimal("0.05")
CENT = Decimal("0.01")


def line_amount(unit_price, quantity):
    """The amount for one line."""
    amount = Decimal(unit_price) * quantity
    return amount


def subtotal(lines, *, member=False):
    """The sum of the lines' amounts, less the member discount."""
    total = sum((line_amount(price, qty) for price, qty in lines), Decimal("0"))
    if member:
        total -= total * MEMBER_DISCOUNT
    return total


def order_total(lines, *, member=False):
    """What the customer pays: the subtotal plus tax, rounded to cents."""
    base = subtotal(lines, member=member)
    return (base * (1 + TAX_RATE)).quantize(CENT, rounding=ROUND_HALF_UP)
