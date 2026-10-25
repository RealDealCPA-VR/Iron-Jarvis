"""Order pricing for the shop.

An order is a list of lines; a line is ``(unit_price, quantity)`` with the
price as a string or a Decimal. README.md states the pricing rules.
"""

from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

TAX_RATE = Decimal("0.08")
MEMBER_DISCOUNT = Decimal("0.05")
CENT = Decimal("0.01")
BULK_MIN_QUANTITY = 10
BULK_DISCOUNT = Decimal("0.10")
MEMBER_MINIMUM = Decimal("20.00")
SHIPPING_FEE = Decimal("4.95")
FREE_SHIPPING_FROM = Decimal("50.00")


def _is_bulk(quantity):
    return quantity >= BULK_MIN_QUANTITY


def line_amount(unit_price, quantity):
    """The amount for one line: unit price times quantity, less BULK_DISCOUNT
    for a line of BULK_MIN_QUANTITY units or more (a per-line adjustment),
    then rounded to cents (half-even) as the LAST step."""
    factor = (1 - BULK_DISCOUNT) if _is_bulk(quantity) else Decimal(1)
    return (Decimal(unit_price) * quantity * factor).quantize(CENT, rounding=ROUND_HALF_EVEN)


def _lines_total(lines):
    total = Decimal("0.00")
    for price, qty in lines:
        total += line_amount(price, qty)
    return total


def _member_applies(lines_total, member):
    return bool(member) and lines_total >= MEMBER_MINIMUM


def subtotal(lines, *, member=False):
    """The sum of the lines' amounts, less the member discount (only when
    that sum is MEMBER_MINIMUM or more)."""
    total = _lines_total(lines)
    if _member_applies(total, member):
        total = total * (1 - MEMBER_DISCOUNT)
    return total


def shipping(amount):
    """The shipping fee for a subtotal: free from FREE_SHIPPING_FROM."""
    if amount >= FREE_SHIPPING_FROM:
        return Decimal("0.00")
    return SHIPPING_FEE


def order_total(lines, *, member=False):
    """What the customer pays: the subtotal plus tax, rounded to cents, plus
    shipping (which is not taxed)."""
    base = subtotal(lines, member=member)
    taxed = (base + base * TAX_RATE).quantize(CENT, rounding=ROUND_HALF_UP)
    return taxed + shipping(base)


def discounts(lines, *, member=False):
    """The discounts an order gets, in words and in this order: one entry
    per bulk line ("bulk: line N", counting lines from 1), then "member"
    when the member discount applies."""
    words = []
    for number, (_price, qty) in enumerate(lines, start=1):
        if _is_bulk(qty):
            words.append("bulk: line %d" % number)
    if _member_applies(_lines_total(lines), member):
        words.append("member")
    return words
