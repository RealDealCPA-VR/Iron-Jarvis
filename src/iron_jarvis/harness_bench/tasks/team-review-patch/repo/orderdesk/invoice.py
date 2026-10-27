"""The invoice text."""

from .discounts import coupon_percent, discount_cents
from .pricing import line_total, money, subtotal
from .shipping import shipping_cents


def render_invoice(customer, lines, coupon=None, *, express=False):
    """The invoice of an order, as text: the customer, one line per order line,
    the subtotal, the discount (when there is one), shipping (marked
    "express" when the order ships express) and the total."""
    out = [f"Invoice for {customer.name} <{customer.email}>"]
    for line in lines:
        amount = line_total(line["price_cents"], line["qty"])
        out.append(f"  {line['qty']} x {line['name']}: {money(amount)}")
    sub = subtotal(lines)
    out.append(f"Subtotal: {money(sub)}")
    percent = coupon_percent(coupon)
    off = discount_cents(sub, percent)
    if off:
        out.append(f"Discount ({percent}%): -{money(off)}")
    ship = shipping_cents(sub, express=express)
    label = "Shipping (express)" if express else "Shipping"
    out.append(f"{label}: {money(ship)}" if ship else f"{label}: free")
    out.append(f"Total: {money(sub - off + ship)}")
    return "\n".join(out)
