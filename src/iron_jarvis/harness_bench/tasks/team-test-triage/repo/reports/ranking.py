"""Top customers and products."""


def top_customers(orders, n=3):
    """The ``n`` customers with the largest total spend (most first; a tie by
    customer id), as ``[(customer_id, total_cents), ...]``."""
    spend = {}
    for order in orders:
        spend[order["customer"]] = spend.get(order["customer"], 0) + order["total_cents"]
    return sorted(spend.items(), key=lambda item: (-item[1], item[0]))[:n]


def repeat_customers(orders):
    """The ids of customers with more than one order, sorted."""
    seen = {}
    for order in orders:
        seen[order["customer"]] = seen.get(order["customer"], 0) + 1
    return sorted(customer for customer, count in seen.items() if count > 1)
