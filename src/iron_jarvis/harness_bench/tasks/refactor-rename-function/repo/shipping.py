import orders

FREE_FROM = 50


def shipping_fee(items):
    """Shipping is free from FREE_FROM upwards, 4.95 below it."""
    total = orders.calc_ttl(items)
    return 0.0 if total >= FREE_FROM else 4.95
