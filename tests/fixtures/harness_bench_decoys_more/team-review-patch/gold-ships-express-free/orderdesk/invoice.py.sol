"""The invoice text."""

from .credit import apply_store_credit
from .discounts import discount_cents, order_discount_percent
from .points import points_earned
from .pricing import line_total, money, subtotal
from .shipping import shipping_cents


def render_invoice(customer, lines, coupon=None, credit_cents=0, *, express=False):
    """The invoice of an order, as text: the customer (and the loyalty tier,
    for silver and gold), one line per order line, the subtotal, the discount
    (when there is one), shipping (marked "express" when the order ships
    express) and the total; then, when store credit pays any of it, the
    credit used and the amount due; and for silver and gold the points the
    order earns."""
    out = [f"Invoice for {customer.name} <{customer.email}>"]
    if customer.tier != "bronze":
        out.append(f"Tier: {customer.tier.capitalize()}")
    for line in lines:
        amount = line_total(line["price_cents"], line["qty"])
        out.append(f"  {line['qty']} x {line['name']}: {money(amount)}")
    sub = subtotal(lines)
    out.append(f"Subtotal: {money(sub)}")
    percent = order_discount_percent(coupon, customer.tier)
    off = discount_cents(sub, percent)
    if off:
        out.append(f"Discount ({percent}%): -{money(off)}")
    ship = shipping_cents(sub, customer.tier, express=express)
    label = "Shipping (express)" if express else "Shipping"
    out.append(f"{label}: {money(ship)}" if ship else f"{label}: free")
    total = sub - off + ship
    out.append(f"Total: {money(total)}")
    used, due = apply_store_credit(total, credit_cents)
    if used:
        out.append(f"Store credit: -{money(used)}")
        out.append(f"Amount due: {money(due)}")
    if customer.tier != "bronze":
        out.append(f"Points earned: {points_earned(sub - off, customer.tier)}")
    return "\n".join(out)
