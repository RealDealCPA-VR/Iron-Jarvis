"""Order pricing for the shop.

An order is a list of lines; a line is ``(unit_price, quantity)`` with the
price as a string or a Decimal. README.md states the pricing rules.
"""

from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

TAX_RATE = Decimal("0.08")
MEMBER_DISCOUNT = Decimal("0.05")
CENT = Decimal("0.01")
SHIPPING_FEE = Decimal("4.95")
FREE_SHIPPING_FROM = Decimal("50.00")
BULK_MIN_QUANTITY = 10
BULK_DISCOUNT = Decimal("0.10")
MEMBER_MINIMUM = Decimal("20.00")


def line_amount(unit_price, quantity):
    """The amount for one line, rounded to cents (half-even). A line of
    BULK_MIN_QUANTITY units or more gets BULK_DISCOUNT off its amount (a
    per-line adjustment).

    Rounding is the LAST step of a line's amount: the per-line adjustment is
    applied before it, so a line's amount is always whole cents.
    """
    amount = Decimal(unit_price) * quantity
    if quantity >= BULK_MIN_QUANTITY:
        amount -= amount * BULK_DISCOUNT
    return amount.quantize(CENT, rounding=ROUND_HALF_EVEN)


def subtotal(lines, *, member=False):
    """The sum of the lines' amounts, less the member discount (only when
    that sum is MEMBER_MINIMUM or more)."""
    total = sum((line_amount(price, qty) for price, qty in lines), Decimal("0"))
    if member and total >= MEMBER_MINIMUM:
        total -= total * MEMBER_DISCOUNT
    return total


def order_total(lines, *, member=False):
    """What the customer pays: the subtotal plus tax, rounded to cents, plus
    shipping (which is not taxed)."""
    base = subtotal(lines, member=member)
    taxed = (base * (1 + TAX_RATE)).quantize(CENT, rounding=ROUND_HALF_UP)
    return taxed + shipping(base)


def shipping(amount):
    """The shipping fee for a subtotal: free from FREE_SHIPPING_FROM."""
    return Decimal("0.00") if amount >= FREE_SHIPPING_FROM else SHIPPING_FEE


def discounts(lines, *, member=False):
    """The discounts an order gets, in words and in this order: one entry
    per bulk line ("bulk: line N", counting lines from 1), then "member"
    when the member discount applies."""
    out = [f"bulk: line {i}" for i, (_price, qty) in enumerate(lines, start=1) if qty >= BULK_MIN_QUANTITY]
    gross = sum((line_amount(price, qty) for price, qty in lines), Decimal("0"))
    if member and gross >= MEMBER_MINIMUM:
        out.append("member")
    return out
