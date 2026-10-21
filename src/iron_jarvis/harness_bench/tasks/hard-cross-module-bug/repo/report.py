"""The order report the shop prints and emails."""

from money import format_cents
from tax import tax_cents


def order_report(order, region):
    lines = []
    for sku, qty, unit, line in order.line_cents():
        lines.append(f"{sku:<10}{qty:>3} x {format_cents(unit):>7} = {format_cents(line):>8}")
    subtotal = order.subtotal_cents()
    tax = tax_cents(subtotal, region)
    lines.append(f"Subtotal: {format_cents(subtotal)}")
    lines.append(f"Tax ({region}): {format_cents(tax)}")
    lines.append(f"Total: {format_cents(subtotal + tax)}")
    return "\n".join(lines)
