from orders import calc_ttl


def invoice_lines(customer, items):
    total = calc_ttl(items)
    return [
        f"Customer: {customer}",
        f"Lines: {len(items)}",
        f"Total: {total:.2f}",
    ]
