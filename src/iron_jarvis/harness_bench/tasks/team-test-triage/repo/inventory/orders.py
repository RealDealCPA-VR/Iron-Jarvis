"""Placing and cancelling orders against the stock."""


def place_order(stock, lines):
    """Reserves every line of an order (a list of ``(sku, qty)``), all or none."""
    done = []
    try:
        for sku, qty in lines:
            stock.reserve(sku, qty)
            done.append((sku, qty))
    except ValueError:
        for sku, qty in done:
            stock.release(sku, qty)
        raise


def cancel_order(stock, lines):
    """Gives back the reservations of a placed order, as its lines were written."""
    for sku, qty in lines:
        stock.release(sku, qty)
