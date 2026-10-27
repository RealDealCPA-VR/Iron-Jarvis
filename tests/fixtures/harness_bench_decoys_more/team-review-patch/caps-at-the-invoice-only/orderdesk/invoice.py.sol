"""The invoice text."""

from .discounts import MAX_DISCOUNT_PERCENT, discount_cents, order_discount_percent
from .points import points_earned
from .pricing import line_total, money, subtotal
from .shipping import shipping_cents


def render_invoice(customer, lines, coupon=None):
    """The invoice of an order, as text: the customer (and the loyalty tier,
    for silver and gold), one line per order line, the subtotal, the discount
    (when there is one), shipping and the total, and for silver and gold the
    points the order earns."""
    out = [f"Invoice for {customer.name} <{customer.email}>"]
    if customer.tier != "bronze":
        out.append(f"Tier: {customer.tier.capitalize()}")
    for line in lines:
        amount = line_total(line["price_cents"], line["qty"])
        out.append(f"  {line['qty']} x {line['name']}: {money(amount)}")
    sub = subtotal(lines)
    out.append(f"Subtotal: {money(sub)}")
    percent = min(order_discount_percent(coupon, customer.tier), MAX_DISCOUNT_PERCENT)
    off = discount_cents(sub, percent)
    if off:
        out.append(f"Discount ({percent}%): -{money(off)}")
    ship = shipping_cents(sub, customer.tier)
    out.append(f"Shipping: {money(ship)}" if ship else "Shipping: free")
    out.append(f"Total: {money(sub - off + ship)}")
    if customer.tier != "bronze":
        out.append(f"Points earned: {points_earned(sub - off, customer.tier)}")
    return "\n".join(out)
