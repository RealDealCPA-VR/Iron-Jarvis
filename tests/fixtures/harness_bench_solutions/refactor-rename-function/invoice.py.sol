from orders import order_total


def invoice_lines(customer, items):
    total = order_total(items)
    return [
        f"Customer: {customer}",
        f"Lines: {len(items)}",
        f"Total: {total:.2f}",
    ]
