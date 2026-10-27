"""The invoice text."""

from .discounts import discount_cents, order_discount_percent
from .pricing import line_total, money, subtotal
from .shipping import shipping_cents


def render_invoice(customer, lines, coupon=None):
    """The invoice of an order, as text: the customer (and the loyalty tier,
    for silver and gold), one line per order line, the subtotal, the discount
    (when there is one), shipping and the total."""
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
    ship = shipping_cents(sub, customer.tier)
    out.append(f"Shipping: {money(ship)}" if ship else "Shipping: free")
    out.append(f"Total: {money(sub - off + ship)}")
    return "\n".join(out)
